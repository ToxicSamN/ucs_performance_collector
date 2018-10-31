
import queue
import multiprocessing
from datetime import datetime
from pyucs.credentials import Credential
from pyucs.ucs import Ucs
from pyucs.statsd.collector import StatsCollector
from pyucs.statsd.parse import Parser
from pyucs.influx import InfluxDB
from pyucs.logging import Logger


LOGGERS = Logger(log_file='/var/log/ucs_perf.log', error_log_file='/var/log/ucs_perf_error.log')


def q_watcher(q1, q2, out_q):
    s_time = datetime.now()
    while True:
        time_delta = (datetime.now()) - s_time
        if q1.qsize() > 0 or q2.qsize() > 0:
            out_q.put_nowait("iq_size: {}, sq_size: {}".format(q1.qsize(), q2.qsize()))

        if time_delta.seconds > 60:
            break


def pool_func(pool_args):
    ucs, data, data_type = pool_args

    if data_type == 'vnic':
        return ucs.get_vnic_stats(data)
    elif data_type == 'vhba':
        return ucs.get_vhba_stats(data)


if __name__ == '__main__':

    queue_manager = multiprocessing.Manager()
    sq = queue_manager.Queue()
    iq = queue_manager.Queue()

    parse_proc = multiprocessing.Process(target=Parser, kwargs={'statsq': sq, 'influxq': iq})
    influx_proc = multiprocessing.Process(target=InfluxDB, kwargs={'influxq': iq,
                                                                   'host': 'y0319t11434',
                                                                   'port': 8086,
                                                                   'username': 'anonymous',
                                                                   'password': 'anonymous',
                                                                   'database': 'perf_stats',
                                                                   'timeout': 5,
                                                                   'retries': 3
                                                                   }
                                          )
    parse_proc.start()
    influx_proc.start()

    ucs_login = {
        'ip': 'ucs0319t04'
    }
    ucs_login.update(
        Credential('oppucs01').get_credential()
    )

    ucs = Ucs(**ucs_login)
    ucs.connect()
    statsd = StatsCollector(ucs)
    statsd.query_stats(sq)
