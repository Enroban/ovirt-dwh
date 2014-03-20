#!/bin/bash

# $1 - the command to execute
# $2 - the database to use
# $3 - db hostname  (default 'localhost' or '')
# $4 - db port (default '5432')
execute_command () {
    local command=${1}
    local dbname=${2}
    local dbhost=${3}
    local dbport=${4}
    local filename=$(mktemp)

    printf "${command}\n" > $filename

    execute_file $filename $dbname $dbhost $dbport

    rm $filename
}

# $1 - the file to execute
# $2 - the database to use
# $3 - db hostname  (default 'localhost' or '')
# $4 - db port (default '5432')
execute_file () {
    local filename=${1}
    local dbname=${2}
    local dbhost=${3}
    local dbport=${4}
    local ret_instead_exit=${5}
    # tuples_only - supress header (column names) and footer  (rows affected) from output.
    # ON_ERROR_STOP - stop on error.
    local cmdline="psql -w --pset=tuples_only=on --set ON_ERROR_STOP=1"
    cmdline="${cmdline} --file=${filename} "

    if [[ -n "${dbname}" ]]; then
        cmdline="${cmdline} --dbname=${dbname} "
    fi

    if [[ -n "${USERNAME}" ]]; then
        cmdline="${cmdline} --username=${USERNAME} "
    fi

    if [[ -n "${dbhost}" ]]; then
        cmdline="${cmdline} --host=${dbhost} "
        if [[ -n "${dbport}" ]]; then
           cmdline="${cmdline} --port=${dbport} "
        fi
    fi


    if $VERBOSE; then
        cmdline="${cmdline} --echo-all "
    fi

    if [[ -n "${LOGFILE}" ]]; then
	cmdline="${cmdline} --log-file=${LOGFILE} "
    fi

    eval $cmdline
    # save last command return value
    retval=$?
    # exit script if command fails.
    if [ $retval -ne 0 -a -z "${ret_instead_exit}" ]; then
        exit $retval
    fi

    return $retval
}

#cleans db by dropping all objects
cleandb() {
# common stored procedures are executed first (for new added functions to be valid)
    file=$(mktemp)
    execute_file "common_sp.sql" ${DATABASE} ${SERVERNAME} ${PORT} > /dev/null
    CMD="select * from generate_drop_all_seq_syntax();"
    execute_command "$CMD" ${DATABASE} ${SERVERNAME} ${PORT} > $file
    CMD="select * from generate_drop_all_tables_syntax();"
    execute_command "$CMD" ${DATABASE} ${SERVERNAME} ${PORT} >> $file
    CMD="select * from generate_drop_all_views_syntax();"
    execute_command "$CMD" ${DATABASE} ${SERVERNAME} ${PORT} >> $file
    CMD="select * from generate_drop_all_functions_syntax();"
    execute_command "$CMD"  ${DATABASE} ${SERVERNAME} ${PORT} >> $file
    execute_file "${file}" ${DATABASE} ${SERVERNAME} ${PORT} > /dev/null
    CMD="DROP FUNCTION IF EXISTS uuid_generate_v1();"
    execute_command "$CMD" ${DATABASE} ${SERVERNAME} ${PORT} > /dev/null
}

#drops views before upgrade or refresh operations
drop_views() {
# common stored procedures are executed first (for new added functions to be valid)
execute_file "common_sp.sql" ${DATABASE} ${SERVERNAME} ${PORT} > /dev/null
    CMD="select * from generate_drop_all_views_syntax();"
    execute_command "$CMD" ${DATABASE} ${SERVERNAME} ${PORT} > drop_all_views.sql
    execute_file "drop_all_views.sql" ${DATABASE} ${SERVERNAME} ${PORT}> /dev/null
    \rm -f drop_all_views.sql
}

drop_old_uuid_functions() {
    #
    # dropping the uuid extension and old functions is
    # required only if we upgrade from old database
    # as it requires special privileges, so
    # before execution check if actually required
    #
    CMD="select count(*) from pg_proc where proname = 'uuid_nil';"
    if [ "$(execute_command "$CMD" ${DATABASE} ${SERVERNAME} ${PORT} | sed -e 's/ //g' -e '/^$/d')" != 0 ]; then
        su postgres -c "psql -f drop_old_uuid_functions.sql ${DATABASE}"  > /dev/null
    fi
}

#drops sps before upgrade or refresh operations
drop_sps() {
# common stored procedures are executed first (for new added functions to be valid)
execute_file "common_sp.sql" ${DATABASE} ${SERVERNAME} ${PORT} > /dev/null
    local drop_all_functions=$(mktemp)

    CMD="select * from generate_drop_all_functions_syntax();"
    execute_command "$CMD"  ${DATABASE} ${SERVERNAME} ${PORT} > "${drop_all_functions}"
    execute_file "${drop_all_functions}" ${DATABASE} ${SERVERNAME} ${PORT} > /dev/null
    \rm -f "${drop_all_functions}"

    drop_old_uuid_functions
    # recreate generic functions
    execute_file "create_functions.sql" ${DATABASE} ${SERVERNAME} ${PORT} > /dev/null
}

#refreshes sps
refresh_sps() {
    printf "Creating stored procedures...\n"
    for sql in $(ls *sp.sql); do
        printf "Creating stored procedures from $sql ...\n"
        execute_file $sql ${DATABASE} ${SERVERNAME} ${PORT} > /dev/null
    done
    execute_file "common_sp.sql" ${DATABASE} ${SERVERNAME} ${PORT} > /dev/null
}

install_common_func() {
    # common stored procedures are executed first (for new added functions to be valid)
    execute_file "common_sp.sql" ${DATABASE} ${SERVERNAME} ${PORT} > /dev/null
}

delete_async_tasks_and_compensation_data() {
    execute_file "delete_async_tasks_and_compensation_data.sql" ${DATABASE} ${SERVERNAME} ${PORT}> /dev/null
}

get_custom_user_permissions() {
    pg_dump -w -s --host="${SERVVERNAME}" --port="${PORT}" --username="${USERNAME}" "${DATABASE}" | \
    sed -n -e '/^grant/Ip' | sed -e "/to \(public\|postgres\)\|${USERNAME};/Id"
}

run_pre_upgrade() {
    #Dropping all views & sps
    drop_views
    drop_sps
    install_common_func
    #run pre upgrade scripts
    execute_commands_in_dir 'pre_upgrade' 'pre-upgrade'
    install_materialized_views_func
    #drop materialized views to support views changesin upgrade
    #Materialized views are restored in the post_upgrade step
    drop_materialized_views

    if [[ -n "${CLEAN_TASKS}" ]]; then
       echo "Cleaning tasks metadata..."
       delete_async_tasks_and_compensation_data
    fi
}

run_post_upgrade() {
    #Refreshing  all views & sps & run post-upgrade scripts
    refresh_views
    refresh_sps
    #Running post-upgrade scripts
    execute_commands_in_dir 'post_upgrade' 'post-upgrade'
    #run custom materialized views if exists
    custom_materialized_views_file="upgrade/post_upgrade/custom/create_materialized_views.sql"
    if [ -f ${custom_materialized_views_file} ]; then
        echo "running custom materialized views from ${custom_materialized_views_file} ..."
        psql -w -U ${USERNAME} --pset=tuples_only=on  --set ON_ERROR_STOP=1 -h ${SERVERNAME} -p ${PORT} -f "${custom_materialized_views_file}" ${DATABASE} > /dev/null
        if [ $? -ne 0 ] ; then
            #drop all custom views
            psql -w -U ${USERNAME} -h ${SERVERNAME} -p ${PORT} -c "select DropAllCustomMaterializedViews();" ${DATABASE} > /dev/null
            echo "Illegal syntax in custom Materialized Views, Custom Materialized Views were dropped."
        fi
    fi
    refresh_materialized_views

}

# Runs all the SQL scripts in directory upgrade/$1/
# The second argument is the label to use while notifying
# the user about the running of the script
execute_commands_in_dir() {
    if [ -d upgrade/$1 ]; then
        files=$(get_files "upgrade/${1}" 1)
        for execFile in $(ls $files | sort); do
           run_file $execFile
        done
    fi
}

run_required_scripts() {
local script=${1}
# check for helper functions that the script needs
# source scripts must be defined in the first lines of the script
while read line; do
expr=$(echo $line | cut -d " " -f1 |grep "\-\-#source")
if [[ -z "${expr}" ]] ; then
   break;
else
   sql=$(echo $line | cut -d " " -f2)
   valid=$(echo $sql | grep "_sp.sql")
   if [[ -z "${valid}" ]]; then
      echo "invalid source file $sql in $file , source files must end with '_sp.sql'"
      exit 1
   fi
   echo "Running helper functions from $sql for $file "
   execute_file $sql ${DATABASE} ${SERVERNAME} ${PORT}  > /dev/null
fi
done < "$script"
}

run_file() {
   local execFile=${1}
   isShellScript=$(file $execFile | grep "shell" | wc -l)
   if [ $isShellScript -gt 0 ]; then
       echo "Running upgrade shell script $execFile ..."
       export  DATABASE="${DATABASE}" SERVERNAME="${SERVERNAME}" PORT="${PORT}" USERNAME="${USERNAME}"
      ./$execFile
   else
      echo "Running upgrade sql script $execFile ..."
      execute_file $execFile ${DATABASE} ${SERVERNAME} ${PORT} > /dev/null
   fi
}

set_version() {
    execute_file upgrade/03_01_0000_set_version.sql ${DATABASE} ${SERVERNAME} ${PORT} > /dev/null
    if [  -n "${VERSION}" ]; then
        CMD="update schema_version set current=true where version=trim('${VERSION}');"
        execute_command "${CMD}" ${DATABASE} ${SERVERNAME} ${PORT} > /dev/null
    fi
}

get_current_version() {
    echo "select version from schema_version where current = true order by id LIMIT 1;" |
                    psql -w -U ${USERNAME} --pset=tuples_only=on ${DATABASE} -h ${SERVERNAME} -p ${PORT}
}

get_installed_version() {
    local cheksum=${1}
    echo "select version from schema_version where checksum = '${cheksum}' and state = 'INSTALLED';" |
                    psql -w -U ${USERNAME} --pset=tuples_only=on ${DATABASE} -h ${SERVERNAME} -p ${PORT}
}

get_last_installed_id() {
    echo "select max(id) from schema_version where state in ('INSTALLED','SKIPPED')" | psql -w -U ${USERNAME} --pset=tuples_only=on ${DATABASE} -h ${SERVERNAME} -p ${PORT}
}
set_last_version() {
    id=$(get_last_installed_id)
    CMD="update schema_version set current=(id=$id);"
    execute_command "${CMD}" ${DATABASE} ${SERVERNAME} ${PORT}> /dev/null
}

get_db_time(){
    echo "select now();" | psql -w -U ${USERNAME} --pset=tuples_only=on ${DATABASE} -h ${SERVERNAME} -p ${PORT}
}

# gets a directory and required depth and return all sql & sh files
get_files() {
    sqlFiles=$(find ${1} -maxdepth ${2} -name "*.sql" -print)
    shFiles=$(find ${1} -maxdepth ${2} -name "*.sh" -print)
    echo ${sqlFiles} " " ${shFiles}
}

is_view_or_sp_changed() {
    files=$(get_files "upgrade" 3)
    md5sum_file="${MD5DIR}/.${DATABASE}.scripts.md5"
    md5sum_tmp_file="${md5sum_file}.tmp"
    md5sum $files create_*views.sql *_sp.sql > ${md5sum_tmp_file}
    diff -s -q "${md5sum_file}" "${md5sum_tmp_file}" >& /dev/null
    result=$?

    #  0 - identical , 1 - differ , 2 - error
    if [ $result -eq 0 ] ; then
        rm -f "${md5sum_tmp_file}"
    else

        # there is a diff or md5 file does not exist
        mv -f "${md5sum_tmp_file}" "${md5sum_file}"
    fi
    return $result
}

validate_version_uniqueness() {
    prev=""
    files=$(get_files "upgrade" 1)
    for file in $(ls -1 $files) ; do
        ver="${file:8:2}${file:11:2}${file:14:4}"
        if [ "$ver" = "$prev" ]; then
            echo "Operation aborted, found duplicate version : $ver"
            exit 1
        fi
        prev=$ver
    done
}

run_upgrade_files() {
    set_version
    res=$(find upgrade/ -name "*" | grep -E ".sql|.sh" | wc -l)
    if [ $res -gt 0 ]; then
        state="FAILED"
        comment=""
        updated=0
        permissions=""
        validate_version_uniqueness
        if [ "${NOMD5}" = "false" ]; then
            is_view_or_sp_changed
        fi

        # Checks if a view or sp file has been changed
        if [[ $? -ne 0 || "${NOMD5}" = "true" ]]; then
            echo "upgrade script detected a change in Config, View or Stored Procedure..."
            echo "Saving custom users permissions on database objects..."
            permissions="$(get_custom_user_permissions)"
            run_pre_upgrade
            updated=1
        fi

        # get current version
        current=$(get_current_version)
        # we should remove leading blank (from select result) and zero in order not to treat number as octal
        last="${current:2:7}"
        disable_gaps_from="3010910"
        files=$(get_files "upgrade" 1)
        for file in $(ls -1 $files); do
            before=$(get_db_time)
            checksum=$(md5sum $file | cut -d " " -f1)
            # upgrade/dd_dd_dddd* => dddddddd
	    ver="${file:8:2}${file:11:2}${file:14:4}"
            if [ "$ver" -gt "$current" ] ; then
                # we should remove leading zero in order not to treat number as octal
                xver="${ver:1:7}"
                # taking major revision , i.e 03010000=>301
                xverMajor="${xver:0:3}"
                lastMajor="${last:0:3}"

                # check for gaps in upgrade
                if [ "$ver" -gt "$disable_gaps_from" ]; then
                    # check gaps only for identical major revisions
                    if [ ${xverMajor} -eq ${lastMajor} ]; then
                        if [ $(($xver - $last)) -gt 10 ]; then
                           set_last_version
                           echo "Illegal script version number ${ver},version should be in max 10 gap from last installed version: 0${last}"
                           echo "Please fix numbering to interval 0$(( $last + 1)) to 0$(( $last + 10)) and run the upgrade script."
                           exit 1
                        fi
                    fi
                fi
                # check if script was already installed with other version name.
                installed_version=$(get_installed_version $checksum)
                if [[ -n "${installed_version}" ]]; then
                    echo "Skipping upgrade script $file, already installed by ${installed_version}"
                    state="SKIPPED"
                    after=$(get_db_time)
                    last=$xver
                    comment="Installed already by ${installed_version}"
                else
                    # force pre upgrade to run in case no md5 change was
                    # found but we still upgrade, like in db restore.
                    if [ $updated -eq 0 ]; then
                       echo "Saving custom users permissions on database objects..."
                       permissions="$(get_custom_user_permissions)"
                       run_pre_upgrade
                       updated=1
                    fi
                    run_required_scripts $file
                    run_file $file
                    code=$?
                    if [ $code -eq 0 ]; then
                        state="INSTALLED"
                        after=$(get_db_time)
                        last=$xver
                        comment=""
                    else
                        set_last_version
                        exit $code
                    fi
                fi
                CMD="insert into schema_version(version,script,checksum,installed_by,started_at,ended_at,state,current,comment)
                     values (trim('$ver'),'$file','$checksum','${USERNAME}',
                     cast(trim('$before') as timestamp),cast(trim('$after') as timestamp),'$state',false,'$comment');"
                psql -w -U ${USERNAME} --pset=tuples_only=on -h ${SERVERNAME} -p ${PORT} -c "${CMD}" ${DATABASE} > /dev/null
            fi
        done
        set_last_version

        # restore views & SPs if dropped
        if [ $updated -eq 1 ]; then
            run_post_upgrade
            echo "Applying custom users permissions on database objects..."
            execute_command "${permissions}" "${DATABASE}" "${SERVERNAME}" "${PORT}" > /dev/null
        else
	    echo "database is up to date."
        fi
    fi
}

pg_version() {
    echo $(psql --version | head -1 | awk '{print $3}')
}

# gets the configuration value of the given option name and version.
# usage: <some variable>=get_config_value <name> <version>
get_config_value() {
   local option_name=${1}
   local version=${2}
   cmd="select option_value from vdc_options where option_name ='${option_name}' and version = '${version}';"
   # remove leading/trailing spaces from the result
   # current implementation of execute_command use --echo-all flag of psql that outputs the query in 1st line
   echo $(execute_command "${cmd}" ${DATABASE} ${SERVERNAME} ${PORT} | sed 's/^ *//g' | head -2 | tail -1 | tr -d ' ')
}

#adds a record to audit_log in case of calling unlock_entity
log_unlock_entity() {
   local object_type=${1}
   local id=${2}
   local user=${3}
   msg="System user ${user} run unlock_entity script on ${object_type} ${id} with db user ${USERNAME}"
   CMD="insert into audit_log(log_time,log_type_name,log_type,severity,message)
        values(now(), 'USER_RUN_UNLOCK_ENTITY_SCRIPT', 2024, 10, '${msg}')"
   execute_command "${CMD}" "${DATABASE}" "${SERVERNAME}" "${PORT}"
}


#unlocks the given VM/Template and its disks or a given disk
#in case of VM/Template the id is the name, in case of a disk, the id is the disk UUID
unlock_entity() {
   local object_type=${1}
   local id=${2}
   local user=${3}
   local recursive=${4}
   if [ ! -n "$recursive" ]; then
       recursive=false
   fi
   CMD=""
   if [ "${object_type}" = "vm" -o "${object_type}" = "template" ]; then
      CMD="select fn_db_unlock_entity('${object_type}', '${id}', ${recursive});"
   elif [ "${object_type}" = "disk" ]; then
      CMD="select fn_db_unlock_disk('${id}');"
   elif [ "${object_type}" = "snapshot" ]; then
      CMD="select fn_db_unlock_snapshot('${id}');"
   else
      printf "Error : $* "
   fi

   if [ "${CMD}" != "" ]; then
       echo "${CMD}"
       execute_command "${CMD}" "${DATABASE}" "${SERVERNAME}" "${PORT}"
       if [ $? -eq 0 ]; then
           log_unlock_entity ${object_type} ${id} ${user}
           printf "unlock ${object_type} ${id} completed successfully.\n"
       else
           printf "unlock ${object_type} ${id} completed with errors.\n"
       fi
   fi
}

#Displays locked entities
query_locked_entities() {
   local object_type=${1}
   LOCKED=2
   TEMPLATE_LOCKED=1
   IMAGE_LOCKED=15;
   SNAPSHOT_LOCKED=LOCKED
   if [ "${object_type}" = "vm" ]; then
       CMD="select vm_name as vm_name from vm_static a ,vm_dynamic b
            where a.vm_guid = b.vm_guid and status = ${IMAGE_LOCKED};"
       psql -w -c "${CMD}" -U ${USERNAME} -d "${DATABASE}" -h "${SERVERNAME}" -p "${PORT}"
       CMD="select vm_name as vm_name , image_group_id as disk_id
            from images a,vm_static b,vm_device c
            where a.image_group_id = c.device_id and b.vm_guid = c.vm_id and
            imagestatus = ${LOCKED} and
            entity_type ilike 'VM' and
            image_group_id in
            (select device_id from vm_device where is_plugged);"
       psql -w -c "${CMD}" -U ${USERNAME} -d "${DATABASE}" -h "${SERVERNAME}" -p "${PORT}"
       CMD="select vm_name as vm_name, snapshot_id as snapshot_id  from vm_static a ,snapshots b
            where a.vm_guid = b.vm_id and status ilike '${SNAPSHOT_LOCKED}';"
       psql -w -c "${CMD}" -U ${USERNAME} -d "${DATABASE}" -h "${SERVERNAME}" -p "${PORT}"
   elif [ "${object_type}" = "template" ]; then
       CMD="select vm_name as template_name from vm_static
                  where template_status = ${TEMPLATE_LOCKED};"
       psql -w -c "${CMD}" -U ${USERNAME} -d "${DATABASE}" -h "${SERVERNAME}" -p "${PORT}"
       CMD="select vm_name as template_name, image_group_id as disk_id
            from images a,vm_static b,vm_device c
            where a.image_group_id = c.device_id and b.vm_guid = c.vm_id and
            imagestatus = ${LOCKED} and
            entity_type ilike 'TEMPLATE' and
            image_group_id in
            (select device_id from vm_device where is_plugged);"
       psql -w -c "${CMD}" -U ${USERNAME} -d "${DATABASE}" -h "${SERVERNAME}" -p "${PORT}"
   elif [ "${object_type}" = "disk" ]; then
       CMD="select vm_id as entity_id,disk_id
            from base_disks a ,images b, vm_device c
            where a.disk_id = b.image_group_id and
                  b.image_group_id = c.device_id and
                  imagestatus = ${LOCKED} and is_plugged;"
       psql -w -c "${CMD}" -U ${USERNAME} -d "${DATABASE}" -h "${SERVERNAME}" -p "${PORT}"
   elif [ "${object_type}" = "snapshot" ]; then
       CMD="select vm_id as entity_id, snapshot_id
            from snapshots a
            where status ilike '${SNAPSHOT_LOCKED}';"
       psql -w -c "${CMD}" -U ${USERNAME} -d "${DATABASE}" -h "${SERVERNAME}" -p "${PORT}"
   fi
}

