#
# ovirt-engine-setup -- ovirt engine setup
# Copyright (C) 2015 Red Hat, Inc.
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


"""Config plugin."""


import os
import gettext


from otopi import util
from otopi import plugin


from ovirt_engine_setup.dwh import constants as odwhcons


def _(m):
    return gettext.dgettext(message=m, domain='ovirt-engine-dwh')


@util.export
class Plugin(plugin.PluginBase):
    """Config plugin."""

    def __init__(self, context):
        super(Plugin, self).__init__(context=context)

    @plugin.event(
        stage=plugin.Stages.STAGE_INIT,
    )
    def _init(self):
        self.environment.setdefault(
            odwhcons.ConfigEnv.OVIRT_ENGINE_DWH_DB_BACKUP_DIR,
            odwhcons.FileLocations.OVIRT_ENGINE_DEFAULT_DWH_DB_BACKUP_DIR
        )

    @plugin.event(
        stage=plugin.Stages.STAGE_VALIDATION,
        condition=lambda self: self.environment[odwhcons.CoreEnv.ENABLE],
    )
    def _validation(self):
        path = self.environment[
            odwhcons.ConfigEnv.OVIRT_ENGINE_DWH_DB_BACKUP_DIR
        ]
        if not os.path.exists(path):
            raise RuntimeError(
                _(
                    'Backup path {path} not found'
                ).format(
                    path=path,
                )
            )


# vim: expandtab tabstop=4 shiftwidth=4
