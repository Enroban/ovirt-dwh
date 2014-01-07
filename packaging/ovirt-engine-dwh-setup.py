#!/usr/bin/python -E
'''
provides an installer for ovirt-engine-dwh
'''

"""
TODO:
1. refactor shared code with ovirt-reports-setup to common_utils
2. check DB connectivity
3. enable command line options
4. refactor to share same UI as ovirt engine setup
"""

import logging
import sys
import os
import time
import traceback
import pwd
import grp
from optparse import OptionParser
import ConfigParser
import getpass
import cracklib
import common_utils as utils
from decorators import transactionDisplay
log_file = None

params = {
    'STOP_ENGINE': None,
    'BACKUP_DB': None,
    'CREATE_READONLY_USER': None,
    'READONLY_USER': None,
    'READONLY_PASS': None,
    'READONLY_SECURE': None,
    'REMOTE_DB_HOST': None,
    'REMOTE_DB_PORT': None,
    'REMOTE_DB_USER': None,
    'REMOTE_DB_PASSWORD': None,
}

OVIRT_SETUP_POST_INSTALL_CONFIG = "/etc/ovirt-engine-setup.conf.d/20-setup-ovirt-post.conf"
DIR_DEPLOY = "/usr/share/ovirt-engine"
DWH_PACKAGE_NAME="ovirt-engine-dwh"
PATH_DB_SCRIPTS="/usr/share/ovirt-engine-dwh/db-scripts"
PATH_WATCHDOG="/usr/share/ovirt-engine-dwh/etl/ovirt_engine_dwh_watchdog.cron"
EXEC_CREATE_SCHEMA="create_schema.sh"
EXEC_UPGRADE_DB="upgrade.sh"
FILE_DB_CONN = "/etc/ovirt-engine/ovirt-engine-dwh/Default.properties"
FILE_ENGINE_CONF_DEFAULTS = "/usr/share/ovirt-engine/conf/engine.conf.defaults"
FILE_ENGINE_CONF = "/etc/ovirt-engine/engine.conf"
FILE_DATABASE_CONFIG = "/etc/ovirt-engine/engine.conf.d/10-setup-database.conf"
DIR_DWH_CONFIG = "/etc/ovirt-engine-dwh"
DIR_DATABASE_DWH_CONFIG = os.path.join(
    DIR_DWH_CONFIG,
    'ovirt-engine-dwhd.conf.d',
)
FILE_DATABASE_DWH_CONFIG = os.path.join(
    DIR_DATABASE_DWH_CONFIG,
    '10-setup-database.conf'
)
DB_BACKUPS_DIR = "/var/lib/ovirt-engine/backups"
DB_NAME = "ovirt_engine_history"
DB_USER = "engine_history"
DB_PORT = "5432"
DB_HOST = "localhost"
PGPASS_TEMP = ''
OVIRT_UID = pwd.getpwnam('ovirt')[2]
OVIRT_GID = grp.getgrnam('ovirt')[2]

# DB messages
DB_FILE = (
    "The DB was backed up as '{dbfile}'"
)
DB_RESTORE = (
    'The DB backup was created with compression. You must use "pg_restore" '
    'command if you need to recover the DB from the backup.'
)

#TODO: Create output messages file with all messages
#TODO: Move all errors here to make consistent usage
# ERRORS:
ERR_DB_CREATE_FAILED = "Error while trying to create %s db" % DB_NAME


def _verifyUserPermissions():
    username = pwd.getpwuid(os.getuid())[0]
    if os.geteuid() != 0:
        sys.exit(
            'Error: insufficient permissions for user {user}, '
            'you must run with user root.'.format(
                user=username
            )
        )

def _parseAnswerFile(answerfile=None):
    if (
        answerfile is not None and
        os.path.exists(answerfile)
    ):
        global params
        fconf = ConfigParser.ConfigParser()
        fconf.read(answerfile)
        for param in params.keys():
            params[param] = fconf.get('general', param)
            if params[param] == 'None':
                params[param] = None
            elif params[param].lower() in ('true', 'yes'):
                params[param] = True
            elif params[param].lower() in ('false', 'no'):
                params[param] = False

    return params


def _getOptions():
    parser = OptionParser()

    parser.add_option(
        "-a",
        "--answer-file",
        dest="answerfile",
        help="Use the following answer file for dwh installation",
    )
    parser.add_option(
        "-g",
        "--gen-answer-file",
        dest="genanswerfile",
        help="Generate answer file",
    )

    (options, args) = parser.parse_args()
    return (options, args)


@transactionDisplay("Creating DB")
def createDbSchema(db_dict):
    """
    create fresh ovirt_engine_history db
    """
    logging.debug("installing %s db", DB_NAME)

    dbLogFilename = "%s/ovirt-history-db-install-%s.log" %("/var/log/ovirt-engine", utils.getCurrentDateTime())
    logging.debug("ovirt engine history db creation is logged at %s" % (dbLogFilename))

    cmd = [
        os.path.join(PATH_DB_SCRIPTS, EXEC_CREATE_SCHEMA),
        '-l', dbLogFilename,
        '-u', db_dict['username'],
        '-s', db_dict['host'],
        '-p', db_dict['port'],
        '-g',
    ]

    # Create db using shell command
    output, rc = utils.execCmd(
        cmdList=cmd,
        failOnError=True,
        msg=ERR_DB_CREATE_FAILED,
        envDict={'ENGINE_PGPASS': PGPASS_TEMP},
    )
    logging.debug('Successfully installed %s DB' % db_dict['dbname'])


@transactionDisplay("Upgrade DB")
def upgradeDB(db_dict):
    """
    upgrade existing ovirt_engine_history db
    """
    logging.debug("upgrading %s db", DB_NAME)
    dbLogFilename = "ovirt-history-db-upgrade-%s.log" %(utils.getCurrentDateTime())
    logging.debug("ovirt engine history db upgrade is logged at %s/%s" % ("/var/log/ovirt-engine", dbLogFilename))

    # Try/Except so we'll be able to return to our current directory
    currDir = os.getcwd()
    try:
        cmd = [
            os.path.join(PATH_DB_SCRIPTS, EXEC_UPGRADE_DB),
            "-s", db_dict["host"],
            "-p", db_dict["port"],
            "-u", db_dict["username"],
            "-d", db_dict['dbname'],
            "-l", "/var/log/ovirt-engine/%s" % dbLogFilename,
            "-g",
        ]
        os.chdir(PATH_DB_SCRIPTS)
        output, rc = utils.execCmd(
            cmdList=cmd,
            failOnError=True,
            msg="Error while trying to upgrade %s DB" % DB_NAME,
            envDict={'ENGINE_PGPASS': PGPASS_TEMP},
        )
    except:
        os.chdir(currDir)
        raise

def getPassFromUser(prompt):
    """
    get a single password from the user
    """
    userInput = getpass.getpass(prompt)
    if len(userInput) == 0:
        print "Cannot accept an empty password"
        return getPassFromUser(prompt)

    try:
        cracklib.FascistCheck(userInput)
    except:
        print "Warning: Weak Password."

    # We do not need verification for the re-entered password
    userInput2 = getpass.getpass("Re-type password: ")
    if userInput != userInput2:
            print "ERROR: passwords don't match"
            return getPassFromUser(prompt)

    return userInput

def getDbCredentials(userdefault=''):
    """
    get db params from user
    """
    print (
        'Remote installation selected. Make sure that DBA creates a user '
        'and the database in the following fashion:\n'
        '\tcreate role <role> with login '
        'encrypted password <password>;\n'
        '\tcreate database ovirt_engine_history template template0 encoding '
        '\'UTF8\' lc_collate \'en_US.UTF-8\' lc_ctype \'en_US.UTF-8\' '
        'owner <role>;\n'
    )

    dbuser = utils.askQuestion(
        question='Provide a remote DB user: ',
        default=userdefault,
    )

    userInput = getPassFromUser(
        prompt='Please choose a password for the db user: '
    )

    return (dbuser, userInput)

def getDbDictFromOptions():
    db_dict = {
        'dbname': DB_NAME,
        'host': utils.getDbHostName(),
        'port': utils.getDbPort(),
        'username': DB_USER,
        'password': utils.generatePassword(),
        'readonly': None,
    }

    for file in (FILE_DATABASE_CONFIG, FILE_DATABASE_DWH_CONFIG):

        if os.path.exists(file):
            handler = utils.TextConfigFileHandler(file)
            handler.open()

            for k, v in (
                ('dbname', 'DWH_DB_DATABASE'),
                ('host', 'ENGINE_DB_HOST'),
                ('port', 'ENGINE_DB_PORT'),
                ('username', 'DWH_DB_USER'),
                ('password', 'DWH_DB_PASSWORD'),
                ('readonly', 'DWH_DB_READONLY_USER'),
                ('engine_db', 'ENGINE_DB_DATABASE'),
                ('engine_user', 'ENGINE_DB_USER'),
                ('engine_pass', 'ENGINE_DB_PASSWORD'),
            ):
                s = handler.getParam(v)
                if s is not None:
                    db_dict[k] = s.strip('"')
            handler.close()

    return db_dict

def getDBStatus(db_dict, TEMP_PGPASS):
    logging.debug("starting getDBStatus")
    exists = owned = hasData = False
    working_db_dict = None
    for dbdict in (
        # Our own user
        db_dict,
        # engine user
        {
            'dbname': DB_NAME,
            'host': db_dict['host'],
            'port': db_dict['port'],
            'username': db_dict['engine_user'],
            'password': db_dict['engine_pass'],
            'engine_user': db_dict['engine_user'],
            'engine_pass': db_dict['engine_pass'],
        },
        # postgres
        {
            'dbname': DB_NAME,
            'host': db_dict['host'],
            'port': db_dict['port'],
            'username': 'admin',
            'password': 'dummy',
            'engine_user': db_dict['engine_user'],
            'engine_pass': db_dict['engine_pass'],
        },
    ):
        tmpexists, tmpowned, tmphasData = utils.dbExists(dbdict, TEMP_PGPASS)
        exists |= tmpexists
        owned |= tmpowned
        hasData |= tmphasData
        if hasData:
            working_db_dict = dbdict
            break
        elif working_db_dict is None and exists:
            working_db_dict = dbdict

        if working_db_dict is not None:
            logging.debug("getDBStatus working username %s" % working_db_dict['username'])
    logging.debug("getDBStatus returns: exists %s owned %s hasData %s" % (exists, owned, hasData))
    return exists, owned, hasData, working_db_dict


@transactionDisplay("Setting DB connectivity")
def setDbPass(db_dict):
    '''
    set the password for the user postgres
    '''
    logging.debug("Setting DB pass")
    logging.debug("editing etl db connectivity file")

    file_handler = utils.TextConfigFileHandler(FILE_DB_CONN)
    file_handler.open()
    file_handler.editParam("ovirtEngineHistoryDbPassword", db_dict["password"])
    file_handler.editParam("ovirtEngineHistoryDbUser", db_dict["username"])
    file_handler.editParam("ovirtEngineDbPassword", db_dict["engine_pass"])
    file_handler.editParam("ovirtEngineDbUser", db_dict["engine_user"])
    file_handler.editParam("ovirtEngineDbJdbcConnection",
                           "jdbc\:postgresql\://{host}\:{port}/{dbname}?stringtype\=unspecified".format(
                               host=db_dict["host"],
                               port=db_dict["port"],
                               dbname=db_dict["engine_db"],
                           ))
    file_handler.editParam("ovirtEngineHistoryDbJdbcConnection",
                           "jdbc\:postgresql\://%s\:%s/%s?stringtype\=unspecified" % (db_dict["host"], db_dict["port"], db_dict['dbname']))
    file_handler.close()
    os.chown(FILE_DB_CONN, 0, OVIRT_GID)
    os.chmod(FILE_DB_CONN, 0o640)

def isVersionSupported(rawMinimalVersion, rawCurrentVersion):
    """
    Check installed version with minimal support version
    """
    # Get current rpm version and parse it.
    (currentVersion, currentMinorVersion, currentRelease) = utils.parseVersionString(rawCurrentVersion)

    # Since minimalETLversion in vdc_options does not contain the "-something" release in its string. we add
    # it in order not to break the parseVersionString interface
    (minimalVersion, minimalMinorVersion, minimalRelease) = utils.parseVersionString("%s-0" % rawMinimalVersion)

    if (float(currentVersion) != float(minimalVersion)) or (int(currentMinorVersion) < int(minimalMinorVersion)):
        return False
    return True

def setVersion():
    """
    set the etlVersion option to current version
    """
    versionString = utils.getAppVersion(DWH_PACKAGE_NAME)
    (currentVersion, currentMinorVersion, currentRelease) = utils.parseVersionString(versionString)
    logging.debug("Setting etlVersion")
    logging.debug("editing etl connectivity file")
    file_handler = utils.TextConfigFileHandler(FILE_DB_CONN)
    file_handler.open()
    file_handler.editParam("etlVersion", "%s.%s" % (currentVersion, currentMinorVersion))
    file_handler.close()
    os.chown(FILE_DB_CONN, 0, OVIRT_GID)
    os.chmod(FILE_DB_CONN, 0o640)

def userExists(user):
    sql_query = '"select 1 from pg_roles where rolname=\'{user}\';"'.format(
        user=user
    )

    output, rc = utils.runPostgresSuQuery(sql_query)
    return '1' in output

def isOvirtEngineInstalled():
    return os.path.exists(OVIRT_SETUP_POST_INSTALL_CONFIG)

def main(options):
    '''
    main
    '''

    os.umask(0022)

    rc = 0
    doBackup = None
    backupFile = None
    pg_updated = False

    readonly_user = options['READONLY_USER']
    readonly_pass = options['READONLY_PASS']
    readonly_secure = options['READONLY_SECURE']

    global PGPASS_TEMP

    try:
        logging.debug("starting main()")
        print "Welcome to ovirt-engine-dwh setup utility\n"

        # Check that oVirt-Engine is installed, otherwise exit gracefully with an informative message
        if not isOvirtEngineInstalled():
            logging.debug("ovirt-engine is not set up, cannot continue")
            print "Please install & setup oVirt engine by executing \"engine-setup\" prior to setting up the data warehouse."
            return 0

        db_dict = getDbDictFromOptions()
        PGPASS_TEMP = utils.createTempPgpass(db_dict)
        for dwh_path in (
            DIR_DWH_CONFIG,
            DIR_DATABASE_DWH_CONFIG
        ):
            if not os.path.exists(dwh_path):
                os.makedirs(dwh_path)
                os.chmod(dwh_path, 0644)

        # Get minimal supported version from oVirt Engine
        minimalVersion = utils.getVDCOption(
            key="MinimalETLVersion",
            db_dict=db_dict,
            temp_pgpass=PGPASS_TEMP,
        )
        currentVersion = utils.getAppVersion(DWH_PACKAGE_NAME)
        if not isVersionSupported(minimalVersion, currentVersion):
            print "Minimal supported version (%s) is higher then installed version (%s), please update the %s package" % (minimalVersion, currentVersion, DWH_PACKAGE_NAME)
            raise Exception("current version not supported by ovirt engine")

        # Stop engine
        if utils.stopEngine(options['STOP_ENGINE']):

            # Stop ETL before doing anything
            utils.stopEtl()

            setVersion()
            readUserCreated = False
            createReadUser = False
            dbExists = owned = hasData = False
            errMsg = ''

            # Create/Upgrade DB
            if utils.localHost(db_dict['host']):
                pg_updated = utils.configHbaIdent()

                # Handle postgres configuration for the read-only user
                # on local installations only

                if db_dict['readonly'] is None:
                    if options['CREATE_READONLY_USER'] is None:
                        # Ask user how would the user be created
                        createReadUser = utils.askYesNo(
                            question=(
                                '\nThis utility can configure a read only user for DB access. '
                                'Would you like to do so?'
                            )
                        )
                    else:
                        createReadUser = options['CREATE_READONLY_USER']

                    if not createReadUser:
                        logging.debug('Skipping creation of read only DB user.')
                        print 'Skipping creation of read only DB user.'
                    elif options['CREATE_READONLY_USER'] is None:
                        readonly_user = ''
                        while not utils.userValid(readonly_user):
                            readonly_user = utils.askQuestion(
                                question='Provide a username for read-only user'
                            )
                        readonly_pass = getPassFromUser(
                            prompt='Provide a password for read-only user: '
                        )
                        readonly_secure = utils.askYesNo(
                            question=(
                                'Should postgresql be setup with secure connection?'
                            )
                        )
                    else:
                        # validate answer file values only
                        if readonly_user is None or not utils.userValid(readonly_user):
                            raise RuntimeError(
                                'Invalid read only user in answer file'
                            )
                        if readonly_pass is None:
                            raise RuntimeError(
                                'Missing password for read only user '
                                'in answer file'
                            )
                        if readonly_secure is None:
                            raise RuntimeError(
                                'Missing parameter READONLY_SECURE '
                                'in answerfile'
                            )

            # Save configuration to the conf.d file
            utils.saveConfig(
                configFile=FILE_DATABASE_DWH_CONFIG,
                username=db_dict['username'],
                password=db_dict['password'],
                dbname=db_dict['dbname'],
                readonly=db_dict['readonly'],
                uid=0,
                gid=OVIRT_GID,
                perms=0o640,
            )

            dbExists, owned, hasData, working_db_dict = getDBStatus(db_dict, PGPASS_TEMP)
            if not utils.localHost(db_dict["host"]):
                # remote
                if hasData:
                    # upgrade
                    db_dict['username'] = working_db_dict['username']
                    db_dict['password'] = working_db_dict['password']
                else:
                    print 'Remote installation is selected.\n'

                    dbExists, tmpowned, tmphasData = utils.dbExists(db_dict, PGPASS_TEMP)
                    if options['REMOTE_DB_USER'] is None:
                        while not dbExists:
                            (
                                db_dict['username'],
                                db_dict['password'],
                            ) = getDbCredentials(
                                 userdefault=db_dict['username'],
                            )
                            if os.path.exists(PGPASS_TEMP):
                                os.remove(PGPASS_TEMP)
                            PGPASS_TEMP = utils.createTempPgpass(db_dict)
                            dbExists, tmpowned, tmphasData = utils.dbExists(db_dict, PGPASS_TEMP)
                            if not dbExists:
                                print 'Could not connect to remote database - please try again.\n'
                    else:
                        db_dict['username'] = options['REMOTE_DB_USER']
                        db_dict['password'] = options['REMOTE_DB_PASSWORD']

                    if os.path.exists(PGPASS_TEMP):
                        os.remove(PGPASS_TEMP)
                    PGPASS_TEMP = utils.createTempPgpass(db_dict)
                    dbExists, owned, hasData, working_db_dict = getDBStatus(db_dict, PGPASS_TEMP)
                    if not dbExists:
                        raise RuntimeError (
                           (
                              'Remote installation failed. Please perform '
                               '\tcreate role {role} with login '
                               'encrypted password {password};\n'
                               '\tcreate {db} owner {role}\n'
                               'on the remote DB, verify it and rerun the setup.'
                            ).format(
                               role=db_dict['username'],
                               db=db_dict['dbname'],
                               password=db_dict['password'],
                            )
                        )

            if dbExists:
                try:
                    if options['BACKUP_DB'] is None:
                        doBackup = utils.performBackup(
                            working_db_dict,
                            DB_BACKUPS_DIR,
                            PGPASS_TEMP
                        )
                    else:
                        doBackup = options['BACKUP_DB']

                    backupFile = os.path.join(
                        DB_BACKUPS_DIR,
                        'ovirt-engine-history.backup.{date}'.format(
                            date=utils.getCurrentDateTime(),
                        )
                    )
                    if doBackup:
                        utils.backupDB(
                            backupFile,
                            working_db_dict,
                            PGPASS_TEMP,
                        )
                        print DB_FILE.format(
                            dbfile=backupFile
                        )
                        print DB_RESTORE
                except UserWarning:
                    print 'User decided to stop setup. Exiting.'
                    # Start Services
                    utils.startEngine()
                    # Sleep for 20 secs to allow health applet to start
                    time.sleep(20)
                    utils.startEtl()
                    sys.exit(0)
                except Exception as e:
                    print 'Failed to backup database: {error}'.format(
                        error=e,
                    )
                    # Start Services
                    utils.startEngine()
                    # Sleep for 20 secs to allow health applet to start
                    time.sleep(20)
                    utils.startEtl()
                    sys.exit(0)

                if utils.localHost(db_dict['host']) and not owned:
                    if not userExists(db_dict['username']):
                        utils.createUser(
                            user=db_dict['username'],
                            password=db_dict['password'],
                            option='createdb',
                            validate=False,
                        )
                    utils.updateDbOwner(db_dict)

                if hasData:
                    # Backup went ok, so upgrade
                    upgradeDB(db_dict)
                else:
                    createDbSchema(db_dict)
            else:
                # db does not exist
                if not userExists(db_dict['username']):
                    utils.createUser(
                        user=db_dict['username'],
                        password=db_dict['password'],
                        option='createdb',
                        validate=False,
                    )

                utils.createDB(db_dict['dbname'], db_dict['username'])
                utils.updatePgHba(db_dict['dbname'], db_dict['username'])
                utils.restartPostgres()
                createDbSchema(db_dict)

            if createReadUser:
                # Create read only
                readUserCreated, errMsg = utils.createReadOnlyUser(
                    db_dict['dbname'],
                    readonly_user,
                    readonly_pass,
                    readonly_secure,
                )

                if not readUserCreated:
                    print (
                        'While trying to create a read-only DB user, '
                        'the following error received: {error}'
                    ).format(
                        error=errMsg
                    )
                else:
                    db_dict['readonly'] = readonly_user

            # Set DB connecitivty (user/pass)
            setDbPass(db_dict)

            if pg_updated:
                utils.restorePgHba()
                time.sleep(2)

            # Start Services
            utils.startEngine()
            # Sleep for 20 secs to allow health applet to start
            time.sleep(20)
            utils.startEtl()

            print "Successfully installed %s." % DWH_PACKAGE_NAME
            print "The installation log file is available at: %s" % log_file

            utils.saveConfig(
                configFile=FILE_DATABASE_DWH_CONFIG,
                username=db_dict['username'],
                password=db_dict['password'],
                dbname=db_dict['dbname'],
                readonly=db_dict['readonly'],
                uid=0,
                gid=OVIRT_GID,
                perms=0o640,
            )

        else:
            logging.debug("user chose not to stop engine")
            print "Installation stopped, Goodbye."

        logging.debug("main() ended")
    except:
        logging.error("Exception caught!")
        logging.error(traceback.format_exc())
        print "Error encountered while installing %s, please consult the log file: %s" % (DWH_PACKAGE_NAME,log_file)
        rc = 1
    finally:
        if os.path.exists(PGPASS_TEMP):
            os.remove(PGPASS_TEMP)

        return rc

if __name__ == "__main__":
    # Check permissions first
    _verifyUserPermissions()

    # Initiate logging
    log_file = utils.initLogging(
        "%s-setup" % DWH_PACKAGE_NAME,
        "/var/log/ovirt-engine"
    )

    options, args = _getOptions()
    if options.genanswerfile:
        with open(options.genanswerfile, 'w') as af:
            content = '[general]\n'
            for param in params.keys():
                content = '{content}{newline}\n'.format(
                    content=content,
                    newline='{key}={value}'.format(
                        key=param,
                        value=params[param],
                    )
                )
            af.write(content)
            print 'Answer file generated at {answerfile}\n'.format(
                answerfile=options.genanswerfile
            )
            sys.exit(0)

    rc = main(_parseAnswerFile(options.answerfile))
    sys.exit(rc)
