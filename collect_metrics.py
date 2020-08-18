VERSION = "1.4.12"

import os
import sys
import time
import argparse
import multiprocessing
import logging
from configparser import ConfigParser
from configparser import NoOptionError
from datetime import datetime
from pyucs.credentials.credstore import Credential
from pyucs.ucs.handler import Ucs
from pyucs.statsd.collector import StatsCollector
from pyucs.statsd.parse import Parser
from pyucs.influx.client import InfluxDB
from log.setup import LoggerSetup
from args.handle import Args

BASE_DIR = os.path.dirname(os.path.realpath(__file__))
parent_dir = BASE_DIR.replace(os.path.basename(BASE_DIR), '')
sys.path.append(BASE_DIR)

args = Args()

log_setup = LoggerSetup(yaml_file=f'{BASE_DIR}/logging_config.yml')
if args.DEBUG:
    log_setup.set_loglevel(loglevel='DEBUG')

log_setup.setup()

logger = logging.getLogger(__name__)


def new_bg_agents(num, sq, iq):
    try:
        proc_pool = []
        # only need a single parser process as this process is as effecient as can be

        for x in range(int(num)):
            proc_pool.append(multiprocessing.Process(name=f'influx_proc_{x}',
                                                     target=InfluxDB,
                                                     kwargs={'influxq': iq,
                                                             'host': args.TelegrafIP,
                                                             'port': args.prod_port,
                                                             'username': 'anonymous',
                                                             'password': 'anonymous',
                                                             'database': 'perf_stats',
                                                             'timeout': 5,
                                                             'retries': 3,
                                                             }
                                                     ))
            proc_pool.append(multiprocessing.Process(name=f'parser_proc_{x}',
                                                     target=Parser,
                                                     kwargs={'statsq': sq, 'influxq': iq}))

        return proc_pool
    except BaseException as e:
        logger.exception(f'Exception: {e}, \n Args: {e.args}')
    return None


def check_bg_process(proc_pool=[], proc=None):

    try:
        if proc:
            if not proc.is_alive():
                logger.debug(f'Process: {proc.name}, Not Alive')
                if proc.pid:
                    logger.debug(f'Process: {proc.name}, PID: {proc.pid}, ExitCode {proc.exitcode}')
                    proc.terminate()
                logger.info(f'Process: {proc.name}, Starting Process')
                proc.start()
        elif proc_pool:
            for proc in proc_pool:
                if not proc.is_alive():
                    logger.debug(f'Process: {proc.name}, Not Alive')
                    if proc.pid:
                        logger.debug(f'Process: {proc.name}, PID: {proc.pid}, ExitCode {proc.exitcode}')
                        proc.terminate()
                    logger.info(f'Process: {proc.name}, Starting Process')
                    proc.start()
    except BaseException as e:
        logger.exception(f'Exception: {e}, \n Args: {e.args}')


def main_worker(func_args):
    """
    Multiprocess main function for collecting on multiple ucs domains at one time
    :param func_args: [ucs_name, statsq]
    :return: none
    """
    ucsm_name, statsq = func_args
    args = Args()
    log = logging.getLogger("__main__.main_worker")
    log.info(f'Starting process worker for ucs {ucsm_name}\n func_args: {func_args}\nARGS: {args.__dict__}')

    try:
        # initialize the Ucs object with the username and password
        log.info(f'Initialize UCS object for {ucsm_name}')
        ucs = Ucs(**{
            'ip': ucsm_name,
            'username': args.username,
            'password': args.get_passwd()
        })
        log.info(f'Connecting to UCS {ucs.ucs}')
        ucs.connect()
        log.info(f'Executing statsd parallelism for {ucs.ucs}')
        # initialize the statsd agent for this ucs
        statsd = StatsCollector(ucs)
        # start the statsd query process
        statsd.query_stats(statsq)
        # disconnect from ucs prior to moving to the next ucs
        ucs.disconnect()
    except BaseException as e:
        log.error(f'Damn!\nQueue size: {statsq.qsize()}, Ucs: {ucs.ucs}')
        log.exception(f'{ucs.ucs}: Exception: {e}, \n Args: {e.args}')
        if ucs._connected:
            ucs.disconnect()

    return 0


def main(statsq, ucs):
    """
    This is the main workhorse of this agent
    :param statsq: multiprocessing.queue
    :return: 0
    """

    # obtain the args
    args = Args()
    # setup logging
    log = logging.getLogger("__main__.main")
    try:
        log.info(f'Starting collect_metrics.py:  ARGS: {args.__dict__}')
        log.info(f'Starting process worker for ucs {ucs.ucs}')

        try:
            log.info(f'Connecting to UCS {ucs.ucs}')
            ucs.connect()

            log.info(f'Executing statsd parallelism for {ucs.ucs}')
            # initialize the statsd agent for this ucs
            statsd = StatsCollector(ucs)
            # start the statsd query process
            statsd.query_stats(statsq)
            # disconnect from ucs prior to moving to the next ucs
            ucs.disconnect()
        except BaseException as e:
            log.error(f'Damn!\nQueue size: {statsq.qsize()}, Ucs: {ucs.ucs}')
            log.exception(f'Exception: {e}, \n Args: {e.args}')
            if ucs._connected:
                ucs.disconnect()

        if ucs._connected:
            ucs.disconnect()
        # return 0

    except BaseException as e:
        log.error(f'{ucs.ucs}: Houston we have a problem!\nQueue size: {statsq.qsize()}')
        log.exception(f'Exception: {e}, \n Args: {e.args}')
        if ucs._connected:
            ucs.disconnect()


def waiter(process_pool, timeout_secs=60):

    start_time = datetime.now()
    proc_status = {}
    for proc in process_pool:
        proc.start()
        logger.info(f'Process: {proc.name}, Started: {proc.is_alive()}')
        proc_status.update({proc.name: proc.is_alive()})

    time.sleep(1)
    # Just going to loop until the timeout has been reached or all processes have completed
    while True:
        # can't just have a while loop without it doing something so....
        for proc in process_pool:
            # track the running status of each process True/False
            proc_status[proc.name] = proc.is_alive()

        # if all of the processes are not running any longer then break
        if list(proc_status.values()).count(False) == len(process_pool):
            logger.info(f'Process Status: {proc_status}')
            break
        # if the timeout value has been reached then break
        elif (datetime.now() - start_time).seconds >= timeout_secs:
            logger.error('Timeout Reached!')
            logger.info(f'Process Status: {proc_status}')
            break


if __name__ == '__main__':

    # retrieve the arguments and environment configs
    args.store_passwd(Credential(args.username).get_credential()['password'])
    logger.info('Code Version : {VERSION}')
    error_count = 0
    main_program_running_threshold = 60
    over_watch_threshold = (24 * (60 * 60)) - 13  # 24 hours x 60 seconds - 13 seconds
    ucs_pool = []

    # Setup the multiprocessing queues
    queue_manager = multiprocessing.Manager()
    # statsd_queue for the parser process
    sq = queue_manager.Queue()
    # influxdb_queue for the inlfuxdb process to send the stats
    iq = queue_manager.Queue()

    # Setup the background processes
    agent_pool = new_bg_agents(multiprocessing.cpu_count(), sq=sq, iq=iq)
    watch_24_start = datetime.now()

    # This code will run as a service and should run in an infinite loop until the service
    # stops the process
    while True:

        watch_24_now = datetime.now()
        watch_delta = watch_24_now - watch_24_start
        if watch_delta.seconds >= over_watch_threshold:
            logging.info(
                f"Overall runtime running {watch_delta.seconds} seconds. Restarting the program to flush memory and processes")
            # Exit the agent.
            # Wait for the influx_q to be flushed
            while not iq.empty():
                logger.debug('Waiting on the influx_q to flush out before restarting the program...')

            # Since the agent should be ran as a service then the agent should automatically be restarted
            for proc in agent_pool:
                proc.terminate()
            sys.exit(-1)

        # check if the background process for parsing and influx are still running
        check_bg_process(proc_pool=agent_pool)

        try:
            # Perform a VERSION check with the code and if there is an update then restart the agent
            with open(os.path.realpath(__file__), 'r') as f:
                line = f.readline()
                f.close()
            code_version = line.split("=")[1].replace('\"', '').strip('\n').strip()
            if not code_version == VERSION:
                logging.info(f"Code Version change from current version {VERSION} to new version {code_version}")
                # Exit the agent.
                # Since the agent should be ran as a service then the agent should automatically be restarted
                sys.exit(-1)

            start_main = True
            if start_main:
                start_main = False
                logger.info('Executing MAIN Processes...')
                # execute the main function as a process so that it can be monitored for running time
                process_pool = []
                ucs_pool = []
                for ucsm in args.ucsNameOrIP:
                    ucs = Ucs(**{
                        'ip': ucsm,
                        'username': args.username,
                        'password': args.get_passwd()
                    })
                    logger.info(f'Connecting to UCS {ucs.ucs}')
                    ucs.connect()
                    ucs_pool.append(ucs)
                    process_pool.append(multiprocessing.Process(target=main, args=(sq, ucs,), name=ucsm))
                start_time = datetime.now()
                # Join the process so that the While loop is halted until the process is complete
                # or times out after 60 seconds
                waiter(process_pool, main_program_running_threshold)

                # if the process has been running for longer than 60 seconds then
                # the program releases control back to root. This is a condition
                # check to see if that is indeed what happened
                for proc in process_pool:
                    if proc.is_alive():
                        # process ran longer than 60 seconds and since collection times are in 60 second intervals
                        # this main process needs to be terminated and restarted
                        proc.terminate()
                        logger.error(
                            f'MAIN process {proc.name} running too long. Start Time: {start_time.ctime()}, End Time: {datetime.now().ctime()}')
                        start_main = True

                        # TODO: add an alerting module that sends an alert either through email or snmp

                end_time = datetime.now()
                logger.info(f'Execution Completed in {(end_time-start_time).seconds} seconds')

            # evaluate the timing to determine how long to sleep
            #  since pulling 1 minutes of perf data then should sleep
            #  sample interval time minus the execution time
            exec_time_delta = end_time - start_time
            sleep_time = main_program_running_threshold - int(exec_time_delta.seconds)
            if sleep_time >= 1:
                logger.info(f'Main program pausing for {sleep_time} seconds')
                time.sleep(sleep_time)
            time.sleep(1)
            error_count = 0
            for u in ucs_pool:
                    u.disconnect()
        except BaseException as e:
            if isinstance(e, SystemExit):
                logging.info('Agent exiting..')
                logging.info('Parser process exiting..')
                for proc in agent_pool:
                    proc.terminate()
                for u in ucs_pool:
                    u.disconnect()
                break
            logger.exception(f'Exception: {e} \n Args: {e.args}')
            start_main = True
            time.sleep(1)
            for u in ucs_pool:
                u.disconnect()
            if error_count > 20:
                for proc in agent_pool:
                    proc.terminate()
                raise e
            else:
                error_count = error_count + 1
                pass
    # final catch all background process termination
    for proc in agent_pool:
        proc.terminate()
    for u in ucs_pool:
        u.disconnect()
