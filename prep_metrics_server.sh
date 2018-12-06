#!/bin/bash
# Prepare the metrics servers with the basic standard configurations 

# NOTE: There are parameters in this script such as $GITUSER and $PROXY_SERVER and GIT_REPO_URL
# that must be set prior to executing this script. This script should be 
# modified for your individual environment as not all environments are the same
# and this particular script is configured for a specific environment.

usage(){
	p_desc="python download link for tar ball (tgz) file."
	t_desc="telegraf link for rpm package file."
	u_desc="\t[required] git user/password information."
	r_desc="\t[required] git repo url for cloning."
	x_desc="\t\t[required] proxy server to reach out to the internet\n\t\t\t\t  to download installs, git clones, and pip installs."
	h_desc="\t\tprovide usage information."
	printf "Usage: \n  $0 [OPTIONS]\n\nOPTIONS:\n"
	printf "  -p | --python-download-link\t$p_desc\n  -t | --telegraf-download-link\t$t_desc\n  -u | --git-user\t$u_desc\n  -r | --git-repo-url\t$r_desc\n  -x | --proxy\t$x_desc\n  -h | --help\t$h_desc\n\n\n"
	exit 1
}

PYTHON_LINK="https://www.python.org/ftp/python/3.7.1/Python-3.7.1.tgz"
TELEGRAF_LINK="https://dl.influxdata.com/telegraf/releases/telegraf-1.9.0-1.x86_64.rpm"

while : 
do
	case "$1" in 
		-p | --python-download-link)	
			shift
			$PYTHON_LINK="$1"
			;;
		-t | --telegraf-download-link)	
			shift
			$TELEGRAF_LINK="$1"
			;;
		-u | --git-user)	
			shift
			GITUSER="$1"
			;;
		-r | --git-repo-url)	
			shift
			GIT_REPO_URL="$1"
			;;
		-x | --proxy)	
			shift
			PROXY_SERVER="$1"
			;;
		-h | --help)	
			shift
			usage
			;;
		--) # end of all options
			shift
			break
			;;
		-*) # unknown option
			echo "Error: Unknown Parameter: $1" >&2
			exit 99
			;;
		* )	# no more options
			break
			;;
	esac
	shift
done

if [ -z $GITUSER ] || [ -z $GIT_REPO_URL ] || [ -z $PROXY_SERVER ];
then
	printf "\nERROR: Required Parameters Not Found\n"
	usage
fi

# Run updates and install baseline packages
sudo yum update -y
sudo yum groupinstall -y "development tools"
sudo yum install -y zlib-devel bzip2-devel openssl-devel ncurses-devel sqlite-devel readline-devel tk-devel gdbm-devel db4-devel libpcap-devel xz-devel expat-devel libffi-devel

# Download and Install Python
cd /tmp
wget $PYTHON_LINK -e use_proxy=yes -e https_proxy=$PROXY_SERVER
tar -xvf Python-3.7.1.tgz
cd Python-3.7.1
./configure --prefix=/usr/local --enable-shared LDFLAGS="-Wl,-rpath /usr/local/lib"
make && sudo make altinstall

# Download and install telegraf
cd /tmp
wget $TELEGRAF_LINK -e use_proxy=yes -e https_proxy=$PROXY_SERVER
sudo rpm -Uvh telegraf-1.9.0-1.x86_64.rpm

# Cleanup tmp
sudo rm -f /tmp/Python-*
sudo rm -Rf /tmp/Python-*
sudo rm -f /tmp/telegraf*

# Need to setup the 3rd hard disk using LVM
#  You should verify that /dev/sdc is the correct device for your 3rd hard disk before continuing
sudo pvcreate /dev/sdc
sudo vgcreate vg01 /dev/sdc
sudo lvcreate -l 100%FREE -n vol01 vg01
sudo mkdir /u01
sudo mkfs.xfs -L /u01 /dev/vg01/vol01
 
ISMOUNTED=$(mount | grep "/u01")
if [ -z "$ISMOUNTED" ]
then
	# Now you will need to edit the /etc/fstab file paste this line at the end
	# /dev/vg01/vol01 /u01    xfs     defaults        0 0
	sudo printf "/dev/vg01/vol01 /u01\txfs\tdefaults\t0 0\n" | sudo tee --append /etc/fstab
	sudo mount /u01
fi

ISMOUNTED=$(mount | grep "/u01")
if [ -z "$ISMOUNTED" ]
then
	echo "/u01 Device not mounted"
	exit
fi

# Create all of the directories
sudo mkdir /u01/tmp
sudo mkdir /u01/log
sudo mkdir /u01/code
sudo mkdir /u01/api
sudo mkdir /u01/api/key
sudo mkdir /etc/metrics
  
# Setup the permissions and owner for smpadmins (internal unix group)
sudo chown -R :smpadmin /u01
sudo chmod 674 -R /u01

# Clone GIT REPO
cd /u01/code
git clone $GIT_REPO_URL
(sudo crontab -l 2>/dev/null; echo "* * * * * /u01/code/ucs_performance_collector/services/git_update.sh") | sudo crontab -

sudo chown -R root:smpadmin /u01/code
sudo chmod 674 -R /u01/code

# Copy all of the Configuration and Daemon files to their forever home
sudo cp -f /u01/code/ucs_performance_collector/services/ucs_metrics.conf /etc/metrics/
sudo cp -f /u01/code/ucs_performance_collector/services/telegraf.conf /etc/telegraf/
sudo cp -f /u01/code/ucs_performance_collector/services/collect-metrics.service /etc/systemd/system/
sudo cp -f /u01/code/ucs_performance_collector/services/startuplast.target /etc/systemd/system/
sudo cp -f /u01/code/ucs_performance_collector/services/telegraf.conf /etc/telegraf/
sudo ln -sf /etc/systemd/system/startuplast.target /etc/systemd/system/default.target

# set the permissions for the API structure
sudo chown -R :smpadmin /u01/api
sudo chmod 674 -R /u01/api
sudo chown -R root:smpadmin /u01/api
sudo chown -R root:smpadmin /u01/api

# Setup the RSA Key Pair
# Generate Random 128 character key
echo $(tr -dc 'A-Za-z0-9!_-' </dev/urandom | head -c 128) | sudo tee /u01/api/key/secret
# store the key into variable
KEY=$(cat /u01/api/key/secret)
# generate RSA private key
sudo openssl genrsa -aes128 -passout pass:$KEY -out /u01/api/key/priv 2048 -noout
# remove the passcode from RSA Private Key
sudo openssl rsa -in /u01/api/key/priv -passin pass:$KEY -out /u01/api/key/priv
# generate public key file
sudo openssl rsa -aes128 -in /u01/api/key/priv -passin pass:$KEY -outform PEM -pubout -out /u01/api/key/pub
KEY=''
ClientId=$(curl https://credstore/credentialstore/NewClientId -k)
ClientId=$(cut -d '"' -f4 <<< $ClientId)
printf "export ClientId=\"$ClientId\"\nexport RSAPrivateFile=\"/u01/api/key/priv\"\nexport RSASecret=\"/u01/api/key/secret\"\n" | sudo tee /etc/environment
printf "ClientId=\"$ClientId\"\nRSAPrivateFile=\"/u01/api/key/priv\"\nRSASecret=\"/u01/api/key/secret\"\nexport ClientId=\$ClientId\nexport RSAPrivateFile=\$RSAPivateFile\nexport RSASecret=\$RSASecret" | sudo tee /etc/profile.d/credstore.sh

# Retrieve the pub contents and the ClientId for registration to the credstore API
cat /u01/api/key/pub && printf "\nClientId: \n$ClientId\n"

sudo chown -R root:root /u01/api/key
sudo chmod -R 644 /u01/api/key

#Setup the Virtual Environment
# Since virtualenv doesn't use proxy setting, lets download pip, setuptools and wheel separately
#  and point virtualenv to these newly downloaded files
sudo mkdir -p /opt/venv/pypi/downloads && cd /opt/venv/pypi/downloads
sudo pip3.7 download --no-cache --proxy $PROXY_SERVER setuptools wheel pip

cd /u01/code/ucs_performance_collector
sudo pip3.7 install virtualenv --proxy $PROXY_SERVER
virtualenv --no-download --extra-search-dir /opt/venv/pypi/downloads venv
source venv/bin/activate
pip install -r requirements.txt --proxy $PROXY_SERVER
cd /u01/code/ucs_performance_collector
