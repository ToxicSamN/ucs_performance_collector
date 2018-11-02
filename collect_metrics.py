VERSION = "1.3.2"

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
    parses througha conf file
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


def main(statsq):
    """
    This is the main workhorse of this agent
    :param statsq: multiprocessing.queue
    :return: 0
    """

    # obtain the args
    args = Args()

    try:
        # setup logging
        main_logger = LOGGERS.get_logger('main')
        main_logger.info('Starting collect_metrics.py:  ARGS: {}'.format(args.__dict__))

        # store the password in encrypted fashion and not clear text
        args.store_passwd(Credential(args.username).get_credential()['password'])

        # the metrics.conf file has the option of using comma separated values
        # so that multiple UCS doamins can be collected on in a single agent.
        # This loops through one or many of those ucs.
        for ucsname in args.ucsNameOrIP:
            # initialize the Ucs object with the username and password
            ucs = Ucs(**{
                'ip': ucsname,
                'username': args.username,
                'password': args.get_passwd()
            })
            main_logger.info('Connecting to UCS {}'.format(ucs.ucs))
            ucs.connect()
            main_logger.info('Executing statsd parallelism for {}'.format(ucs.ucs))
            # initialize the statsd agent for this ucs
            statsd = StatsCollector(ucs)
            # start the statsd query process
            statsd.query_stats(statsq)
            # disconnect from ucs prior to moving to the next ucs
            ucs.disconnect()
        return 0

    except BaseException as e:
        main_logger.exception('Exception: {}, \n Args: {}'.format(e, e.args))
        if ucs._connected:
            ucs.disconnect()


if __name__ == '__main__':

    # retrieve the arguments and environment configs
    args = Args()
    root_logger = LOGGERS.get_logger(__name__)
    root_logger.info('Code Version : {}'.format(VERSION))
    error_count = 0
    main_program_running_threshold = 60

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
                logging.exception("Code Version change from current version {} to new version {}".format(VERSION,
                                                                                                         code_version))
                # Exit the agent.
                # Since the agent should be ran as a service then the agent should automatically be restarted
                sys.exit(-1)

            start_main = True
            if start_main:
                start_main = False
                start_time = datetime.now()
                root_logger.info('Executing MAIN...')
                # execute the main function as a process so that it can be monitored for running time
                main_proc = multiprocessing.Process(target=main, args=(sq,))
                main_proc.start()
                # Join the process so that the While loop is halted until the process is complete
                # or times out after 60 seconds
                main_proc.join(main_program_running_threshold)

                # if the process has been running for longer than 60 seconds then
                # the program releases control back to root. This is a condition
                # check to see if that is indeed what happened
                if main_proc.is_alive():
                    # process ran longer than 60 seconds and since collection times are in 60 second intervals
                    # this main process needs to be terminated and restarted
                    main_proc.terminate()
                    root_logger.error(
                        'MAIN program running too long. Start Time: {}, End Time: {}'.format(start_time.ctime(),
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
        except BaseException as e:
            if isinstance(e, SystemExit):
                logging.info('Agent exiting..')
                logging.info('Parser process exiting..')
                parse_proc.terminate()
                logging.info('InfluxDB process exiting..')
                influx_proc.terminate()
                break
            root_logger.exception('Exception: {} \n Args: {}'.format(e, e.args))
            start_main = True
            time.sleep(1)
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
