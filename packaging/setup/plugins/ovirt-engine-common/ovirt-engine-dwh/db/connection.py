#
# ovirt-engine-setup -- ovirt engine setup
# Copyright (C) 2013 Red Hat, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#


"""Connection plugin."""


import gettext
_ = lambda m: gettext.dgettext(message=m, domain='ovirt-engine-dwh')


from otopi import constants as otopicons
from otopi import util
from otopi import plugin


from ovirt_engine import configfile


from ovirt_engine_setup import constants as osetupcons
from ovirt_engine_setup import dwhconstants as odwhcons
from ovirt_engine_setup import database


@util.export
class Plugin(plugin.PluginBase):
    """Connection plugin."""

    def __init__(self, context):
        super(Plugin, self).__init__(context=context)

    @plugin.event(
        stage=plugin.Stages.STAGE_BOOT,
    )
    def _boot(self):
        self.environment[
            otopicons.CoreEnv.LOG_FILTER_KEYS
        ].append(
            odwhcons.DBEnv.PASSWORD
        )

    @plugin.event(
        stage=plugin.Stages.STAGE_INIT,
    )
    def _init(self):
        self.environment.setdefault(
            odwhcons.DBEnv.HOST,
            None
        )
        self.environment.setdefault(
            odwhcons.DBEnv.PORT,
            None
        )
        self.environment.setdefault(
            odwhcons.DBEnv.SECURED,
            None
        )
        self.environment.setdefault(
            odwhcons.DBEnv.SECURED_HOST_VALIDATION,
            None
        )
        self.environment.setdefault(
            odwhcons.DBEnv.USER,
            None
        )
        self.environment.setdefault(
            odwhcons.DBEnv.PASSWORD,
            None
        )
        self.environment.setdefault(
            odwhcons.DBEnv.DATABASE,
            None
        )

        self.environment[odwhcons.DBEnv.CONNECTION] = None
        self.environment[odwhcons.DBEnv.STATEMENT] = None
        self.environment[odwhcons.DBEnv.NEW_DATABASE] = True

    @plugin.event(
        stage=plugin.Stages.STAGE_SETUP,
    )
    def _commands(self):
        dbovirtutils = database.OvirtUtils(
            plugin=self,
            dbenvkeys=odwhcons.Const.DWH_DB_ENV_KEYS,
        )
        dbovirtutils.detectCommands()

    @plugin.event(
        stage=plugin.Stages.STAGE_SETUP,
        name=odwhcons.Stages.DB_CONNECTION_SETUP,
        condition=lambda self: (
            self.environment[odwhcons.CoreEnv.ENABLE]
        ),
    )
    def _setup(self):
        config = configfile.ConfigFile([
            odwhcons.FileLocations.OVIRT_ENGINE_DWHD_SERVICE_CONFIG_DEFAULTS,
            odwhcons.FileLocations.OVIRT_ENGINE_DWHD_SERVICE_CONFIG,
        ])
        if (
            config.get('DWH_DB_PASSWORD') or
            self.environment.get(odwhcons.DBEnv.PASSWORD)
        ):
            try:
                dbenv = {}
                for e, k in (
                    (odwhcons.DBEnv.HOST, 'DWH_DB_HOST'),
                    (odwhcons.DBEnv.PORT, 'DWH_DB_PORT'),
                    (odwhcons.DBEnv.USER, 'DWH_DB_USER'),
                    (odwhcons.DBEnv.PASSWORD, 'DWH_DB_PASSWORD'),
                    (odwhcons.DBEnv.DATABASE, 'DWH_DB_DATABASE'),
                ):
                    dbenv[e] = self.environment.get(e, config.get(k))
                for e, k in (
                    (odwhcons.DBEnv.SECURED, 'DWH_DB_SECURED'),
                    (
                        odwhcons.DBEnv.SECURED_HOST_VALIDATION,
                        'DWH_DB_SECURED_VALIDATION'
                    )
                ):
                    dbenv[e] = config.getboolean(k)

                dbovirtutils = database.OvirtUtils(
                    plugin=self,
                    dbenvkeys=odwhcons.Const.DWH_DB_ENV_KEYS,
                )
                dbovirtutils.tryDatabaseConnect(dbenv)
                self.environment.update(dbenv)
                self.environment[
                    odwhcons.DBEnv.NEW_DATABASE
                ] = dbovirtutils.isNewDatabase()
            except RuntimeError as e:
                self.logger.debug(
                    'Existing credential use failed',
                    exc_info=True,
                )
                msg = _(
                    'Cannot connect to DWH database using existing '
                    'credentials: {user}@{host}:{port}'
                ).format(
                    host=dbenv[odwhcons.DBEnv.HOST],
                    port=dbenv[odwhcons.DBEnv.PORT],
                    database=dbenv[odwhcons.DBEnv.DATABASE],
                    user=dbenv[odwhcons.DBEnv.USER],
                )
                if self.environment[
                    osetupcons.CoreEnv.ACTION
                ] == osetupcons.Const.ACTION_REMOVE:
                    self.logger.warning(msg)
                else:
                    raise RuntimeError(msg)


# vim: expandtab tabstop=4 shiftwidth=4
