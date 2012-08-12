#!/bin/bash -x
#

LOGFILE="/var/log/ovirt-engine/ovirt-engine-dwhd.log"
ETL_HOME=/usr/share/ovirt-engine-dwh/etl
JAVA_DIR=/usr/share/java
CP=$ETL_HOME:$JAVA_DIR/ovirt-engine-dwh/historyETLProcedure.jar:$JAVA_DIR/ovirt-engine-dwh/advancedPersistentLookupLib.jar:$JAVA_DIR/ovirt-engine-dwh/talendRoutines.jar:$JAVA_DIR/dom4j.jar:$JAVA_DIR/commons-collections.jar:$JAVA_DIR/log4j.jar:$JAVA_DIR/postgresql-jdbc.jar

exec java -Xms256M -Xmx1024M -cp $CP ovirt_engine_dwh.historyetl_3_1.HistoryETL --context=Default $* >> $LOGFILE 2>&1 &
echo $! >$ETL_PID
