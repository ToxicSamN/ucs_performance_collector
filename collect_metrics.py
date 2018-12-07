VERSION = "1.4.1"

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
from pyucs.logging.handler import Logger
from pycrypt.encryption import AESCipher


# Global Loggers variable
LOGGERS = Logger(log_file='/var/log/ucs_perf.log', error_log_file='/var/log/ucs_perf_error.log')


class Args:
    """
    Args Class handles the cmdline arguments passed to the code and
    parses through a conf file
    Usage can be stored to a variable or called by Args().<property>
    """
    DEBUG = False
    MOREF_TYPE = ''
    LOG_DIR = ''
    LOG_SIZE = ''
    MAX_KEEP = ''

    def __init__(self):
        self.__aes_key = None

        # Retrieve and set script arguments from commandline
        parser = argparse.ArgumentParser(description="Performance Collector Agent.")
        parser.add_argument('-debug', '--debug',
                            required=False, action='store_true',
                            help='Used for Debug level information')
        parser.add_argument('-c', '--config-file', default='/etc/metrics/metrics.conf',
                            required=False, action='store',
                            help='identifies location of the config file')
        cmd_args = parser.parse_args()

        # Parse through the provided conf
        parser = ConfigParser()
        parser.read(cmd_args.config_file)

        # [GLOBAL]
        self.bin = str(parser.get('global', 'WorkingDirectory'))
        self.tmpdir = str(parser.get('global', 'TempDirectory'))

        # [LOGGING]
        self.LOG_DIR = str(parser.get('logging', 'LogDir'))
        self.LOG_SIZE = parser.get('logging', 'LogRotateSizeMB')
        self.MAX_KEEP = parser.get('logging', 'MaxFilesKeep')
        self.secdir = parser.get('global', 'SecureDir')
        try:
            debug_check = parser.get('logging', 'Debug')
            if debug_check == 'True':
                self.DEBUG = True
        except NoOptionError:
            pass

        # [INFLUXDB]
        self.TelegrafIP = parser.get('influxdb', 'TelegrafIP')
        self.nonprod_port = parser.get('influxdb', 'nonprod_port')
        self.prod_port = parser.get('influxdb', 'prod_port')

        # [METRICS]
        self.ucsNameOrIP = parser.get('metrics', 'ucsNameOrIP')
        self.ucsNameOrIP = [u.strip() for u in self.ucsNameOrIP.split(',')]
        self.username = parser.get('metrics', 'username')
        self.__password = parser.get('metrics', 'password')
        if self.__password:
            self.store_passwd()

    def get_passwd(self):
        """
        Returns the stored encrypted password from memory
        :return: clear_text password
        """
        if self.__password:
            aes_cipher = AESCipher()
            return aes_cipher.decrypt(self.__password, self.__aes_key)

    def store_passwd(self, clr_passwd):
        """
        Takes the clear text password and stores it in a variable with AES encryption.
        :param clr_passwd:
        :return: None, stores the password in the protected __ variable
        """
        aes_cipher = AESCipher()
        self.__aes_key = aes_cipher.AES_KEY
        self.__password = aes_cipher.encrypt(clr_passwd)


def main_worker(func_args):
    """
    Multiprocess main function for collecting on multiple ucs domains at one time
    :param func_args: [ucs_name, statsq]
    :return: none
    """
    ucsm_name, statsq, logger = func_args
    args = Args()
    logger.info(
        'Starting process worker for ucs {}\n func_args: {}\nARGS: {}'.format(ucsm_name,
                                                                              func_args,
                                                                              args.__dict__))

    try:
        # initialize the Ucs object with the username and password
        logger.info('Initialize UCS object for {}'.format(ucsm_name))
        ucs = Ucs(**{
            'ip': ucsm_name,
            'username': args.username,
            'password': args.get_passwd()
        })
        logger.info('Connecting to UCS {}'.format(ucs.ucs))
        ucs.connect()
        logger.info('Executing statsd parallelism for {}'.format(ucs.ucs))
        # initialize the statsd agent for this ucs
        statsd = StatsCollector(ucs)
        # start the statsd query process
        statsd.query_stats(statsq)
        # disconnect from ucs prior to moving to the next ucs
        ucs.disconnect()
    except BaseException as e:
        logger.error('Damn!\nQueue size: {}, Ucs: {}'.format(statsq.qsize(), ucs.ucs))
        logger.exception('Exception: {}, \n Args: {}'.format(e, e.args))
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
    logger = LOGGERS.get_logger('main')
    try:

        logger.info('Starting collect_metrics.py:  ARGS: {}'.format(args.__dict__))

        args = Args()
        logger.info('Starting process worker for ucs {}'.format(ucs.ucs))

        try:
            # initialize the Ucs object with the username and password
            # logger.info('Initialize UCS object for {}'.format(ucsm_name))
            # ucs = Ucs(**{
            #     'ip': ucsm_name,
            #     'username': args.username,
            #     'password': args.get_passwd()
            # })
            logger.info('Connecting to UCS {}'.format(ucs.ucs))
            ucs.connect()
            logger.info('Executing statsd parallelism for {}'.format(ucs.ucs))
            # initialize the statsd agent for this ucs
            statsd = StatsCollector(ucs)
            # start the statsd query process
            statsd.query_stats(statsq)
            # disconnect from ucs prior to moving to the next ucs
            ucs.disconnect()
        except BaseException as e:
            logger.error('Damn!\nQueue size: {}, Ucs: {}'.format(statsq.qsize(), ucs.ucs))
            logger.exception('Exception: {}, \n Args: {}'.format(e, e.args))
            if ucs._connected:
                ucs.disconnect()

        if ucs._connected:
            ucs.disconnect()
        return 0

    except BaseException as e:
        logger.error('Houston we have a problem!\nQueue size: {}'.format(statsq.qsize()))
        logger.exception('Exception: {}, \n Args: {}'.format(e, e.args))
        if ucs._connected:
            ucs.disconnect()


def waiter(process_pool, timeout_secs=60):

    logger = LOGGERS.get_logger('Process Waiter')
    start_time = datetime.now()
    proc_status = {}
    for proc in process_pool:
        proc.start()
        logger.info('Process: {}, Started: {}'.format(proc.name, proc.is_alive()))
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
            logger.info('Process Status: {}'.format(proc_status))
            break
        # if the timeout value has been reached then break
        elif (datetime.now() - start_time).seconds >= timeout_secs:
            logger.error('Timeout Reached!')
            logger.info('Process Status: {}'.format(proc_status))
            break


if __name__ == '__main__':

    # root = logging.getLogger()
    # root.setLevel(logging.DEBUG)
    # handler = logging.StreamHandler(sys.stdout)
    # handler.setLevel(logging.DEBUG)
    # formatter = logging.Formatter("%(asctime)s\t%(name)s\t%(levelname)s\t%(message)s")
    # handler.setFormatter(formatter)
    # root.addHandler(handler)

    # retrieve the arguments and environment configs
    args = Args()
    args.store_passwd(Credential(args.username).get_credential()['password'])
    root_logger = LOGGERS.get_logger(__name__)
    root_logger.info('Code Version : {}'.format(VERSION))
    error_count = 0
    main_program_running_threshold = 60
    ucs_pool = []

    # Setup the multiprocessing queues
    queue_manager = multiprocessing.Manager()
    # statsd_queue for the parser process
    sq = queue_manager.Queue()
    # influxdb_queue for the inlfuxdb process to send the stats
    iq = queue_manager.Queue()

    # Setup the background processes
    # parser process
    parse_proc = multiprocessing.Process(target=Parser, kwargs={'statsq': sq, 'influxq': iq})
    # influxdb process
    influx_proc = multiprocessing.Process(target=InfluxDB, kwargs={'influxq': iq,
                                                                   'host': args.TelegrafIP,
                                                                   'port': args.prod_port,
                                                                   'username': 'anonymous',
                                                                   'password': 'anonymous',
                                                                   'database': 'perf_stats',
                                                                   'timeout': 5,
                                                                   'retries': 3
                                                                   }
                                          )
    # start the background processes
    root_logger.info('Starting parser subprocess')
    parse_proc.start()
    root_logger.info('Starting influxdb subprocess')
    influx_proc.start()

    # This code will run as a service and should run in an infinite loop until the service
    # stops the process
    while True:

        # check if the background process for parsing and influx are still running
        if not parse_proc.is_alive():
            parse_proc.start()
        if not influx_proc.is_alive():
            influx_proc.start()

        try:
            # Perform a VERSION check with the code and if there is an update then restart the agent
            with open(os.path.realpath(__file__), 'r') as f:
                line = f.readline()
                f.close()
            code_version = line.split("=")[1].replace('\"', '').strip('\n').strip()
            if not code_version == VERSION:
                logging.info("Code Version change from current version {} to new version {}".format(VERSION,
                                                                                                    code_version))
                # Exit the agent.
                # Since the agent should be ran as a service then the agent should automatically be restarted
                sys.exit(-1)

            start_main = True
            if start_main:
                start_main = False
                start_time = datetime.now()
                root_logger.info('Executing MAIN Processes...')
                # execute the main function as a process so that it can be monitored for running time
                process_pool = []
                ucs_pool = []
                for ucsm in args.ucsNameOrIP:
                    ucs = Ucs(**{
                        'ip': ucsm,
                        'username': args.username,
                        'password': args.get_passwd()
                    })
                    ucs_pool.append(ucs)
                    process_pool.append(multiprocessing.Process(target=main, args=(sq, ucs,), name=ucsm))
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
                        root_logger.error(
                            'MAIN process {} running too long. Start Time: {}, End Time: {}'.format(proc.name,
                                                                                                    start_time.ctime(),
                                                                                                    datetime.now().ctime()))
                        start_main = True

                        # TODO: add an alerting module that sends an alert either through email or snmp

                end_time = datetime.now()
                root_logger.info('Execution Completed in {} seconds'.format((end_time-start_time).seconds))

            # evaluate the timing to determine how long to sleep
            #  since pulling 1 minutes of perf data then should sleep
            #  sample interval time minus the execution time
            exec_time_delta = end_time - start_time
            sleep_time = main_program_running_threshold - int(exec_time_delta.seconds)
            if sleep_time >= 1:
                root_logger.info('Main program pausing for {} seconds'.format(sleep_time))
                time.sleep(sleep_time)
            time.sleep(1)
            error_count = 0
            for u in ucs_pool:
                if u._connected:
                    u.disconnect()
        except BaseException as e:
            if isinstance(e, SystemExit):
                logging.info('Agent exiting..')
                logging.info('Parser process exiting..')
                parse_proc.terminate()
                logging.info('InfluxDB process exiting..')
                influx_proc.terminate()
                for u in ucs_pool:
                    if u._connected:
                        u.disconnect()
                break
            root_logger.exception('Exception: {} \n Args: {}'.format(e, e.args))
            start_main = True
            time.sleep(1)
            for u in ucs_pool:
                if u._connected:
                    u.disconnect()
            if error_count > 20:
                parse_proc.terminate()
                influx_proc.terminate()
                raise e
            else:
                error_count = error_count + 1
                pass
    # final catch all background process termination
    parse_proc.terminate()
    influx_proc.terminate()
    for u in ucs_pool:
        if u._connected:
            u.disconnect()
