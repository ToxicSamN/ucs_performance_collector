
import argparse
from pycrypt.encryption import AESCipher
from configparser import ConfigParser
from configparser import NoOptionError


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
        parser.add_argument('-c', '--config-file', default='/etc/metrics/ucs_metrics.conf',
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
