#!/bin/bash
#Launch the collect_metrics.py script 

cd /u01/code/ucs_performance_collector
source /u01/code/ucs_performance_collector/venv/bin/activate

COMMAND="python collect_metrics.py --config-file /etc/metrics/ucs_metrics.conf"
	
RETURN=$($COMMAND)
