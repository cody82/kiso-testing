##########################################################################
# Copyright (c) 2010-2022 Robert Bosch GmbH
# This program and the accompanying materials are made available under the
# terms of the Eclipse Public License 2.0 which is available at
# http://www.eclipse.org/legal/epl-2.0.
#
# SPDX-License-Identifier: EPL-2.0
##########################################################################

import pathlib
import subprocess
import sys
import threading
import time
from unittest import mock

import pytest

from pykiso import message
from pykiso.lib.auxiliaries.zephyr import ZephyrError, ZephyrTestAuxiliary


@pytest.mark.parametrize(
    "cc_receive",
    [
        (
            {
                "msg": {
                    "stderr": "DEBUG   - OUTPUT:  PASS - test_assert in 0.000 seconds\n"
                }
            }
        ),
        (
            {
                "msg": {
                    "stderr": "DEBUG   - OUTPUT:  FAIL - test_assert in 0.000 seconds\n"
                }
            }
        ),
    ],
)
def test_zephyr_aux_new(mocker, cc_receive):

    et_mock = mocker.patch("xml.etree.ElementTree.parse")
    connector = mock.MagicMock()
    connector.cc_receive.side_effect = [
        {"msg": {"stderr": "INFO    - Building initial testsuite list...\n"}},
        {"msg": {"stderr": "DEBUG   - OUTPUT: \n"}},
        {"msg": {"stderr": "DEBUG   - OUTPUT: START - test_assert\n"}},
        cc_receive,
        {"msg": {"exit": 0}},
        {"msg": {"msg": None}},
    ]
    aux = ZephyrTestAuxiliary(connector, "test_dir", "test_name", True)
    assert aux._create_auxiliary_instance()
    aux.start_test()
    aux.wait_test()
    assert aux._delete_auxiliary_instance()


def test_zephyr_aux_start_test_exceptions(mocker):
    aux = ZephyrTestAuxiliary()
    with pytest.raises(ZephyrError) as e:
        aux.start_test()
    with pytest.raises(ZephyrError) as e:
        aux.start_test("test_directory")
    with pytest.raises(ZephyrError) as e:
        aux.running = True
        aux.start_test("test_directory", "test_name")
        aux.running = False


def test_zephyr_aux_wait_test_exceptions(mocker):
    aux = ZephyrTestAuxiliary()
    with pytest.raises(ZephyrError) as e:
        aux.wait_test()


def test_zephyr_junit(mocker):
    xml1 = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
    <!--Name of the root tag does not matter, but it must not be same as the ones below -->
    <!-- testsuite tags can be nested, timestamp is not required and format is "yyyy-MM-dd'T'HH:mm:ssZ" -->
    <testsuite>
        <testcase name="someMethod" classname="SomeClass" time="0.285">
        </testcase>
    </testsuite>
</testsuites>
"""
    xml2 = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
    <!--Name of the root tag does not matter, but it must not be same as the ones below -->
    <!-- testsuite tags can be nested, timestamp is not required and format is "yyyy-MM-dd'T'HH:mm:ssZ" -->
    <testsuite>
        <testcase name="someMethod" classname="SomeClass" time="0.285">
                    <failure message="failure message" type="package.Exception">
                <!-- message and type are not required, all text content is added to the created defect -->
                Failure details
            </failure>
        </testcase>
    </testsuite>
</testsuites>
"""
    xml3 = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
    <!--Name of the root tag does not matter, but it must not be same as the ones below -->
    <!-- testsuite tags can be nested, timestamp is not required and format is "yyyy-MM-dd'T'HH:mm:ssZ" -->
    <testsuite>
        <testcase name="someMethod" classname="SomeClass" time="0.285">
        <error message="error message" type="package.OtherException">
                Error Details
            </error>
        </testcase>
    </testsuite>
</testsuites>
"""
    xml4 = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
    <!--Name of the root tag does not matter, but it must not be same as the ones below -->
    <!-- testsuite tags can be nested, timestamp is not required and format is "yyyy-MM-dd'T'HH:mm:ssZ" -->
    <testsuite>
        <testcase name="someMethod" classname="SomeClass" time="0.285">
        <skipped message="error message" type="package.OtherException">
                Error Details
            </skipped>
        </testcase>
    </testsuite>
</testsuites>
"""
    for xml in [xml1, xml2, xml3, xml4]:
        aux = ZephyrTestAuxiliary()
        with open("tmp_junit.xml", "w") as f:
            f.write(xml)
        aux._parse_xunit("tmp_junit.xml")
        # remove("tmp_junit.xml")
