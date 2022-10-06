##########################################################################
# Copyright (c) 2010-2022 Robert Bosch GmbH
# This program and the accompanying materials are made available under the
# terms of the Eclipse Public License 2.0 which is available at
# http://www.eclipse.org/legal/epl-2.0.
#
# SPDX-License-Identifier: EPL-2.0
##########################################################################
import enum
import logging
import queue
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from pykiso import CChannel, SimpleAuxiliaryInterface
from pykiso.interfaces.dt_auxiliary import (
    DTAuxiliaryInterface,
    close_connector,
    open_connector,
)
from pykiso.types import MsgType

log = logging.getLogger(__name__)


class TestResult(enum.IntEnum):
    """Result of a twister test"""

    PASSED = 0
    FAILED = 1
    SKIPPED = 2
    ERROR = 3


class ZephyrError(Exception):
    """Exception for twister errors"""

    pass


class Twister:
    """Control class for the Zephyr twister commandline tool"""

    def __init__(self, twister_path: str = "twister") -> None:
        self.twister_path = twister_path
        self.process: Optional[subprocess.Popen[bytes]] = None

    def start_test(
        self, test_directory: str, testcase_name: str, wait_for_start: bool = True
    ) -> None:
        """Start the test case

        :param test_directory: The directory to search for the Zephyr test project (passed to twister using the -T parameter)
        :param testcase_name: The name of the Zephyr test (passed to twister using the -s parameter)
        :param wait_for_start: Wait for the test case to start on the target. If this is set to false the function will just return even if the test is not running yet.

        :return: True if successful
        """
        if self.process is not None:
            raise ZephyrError("Twister test is already running")

        self.outdir = str(Path(test_directory).resolve() / "twister-out")
        logging.info(f"Using Twister output directory: {self.outdir}")

        self.process = subprocess.Popen(
            [
                self.twister_path,
                "-vv",
                "-T",
                test_directory,
                "-s",
                testcase_name,
                "--outdir",
                self.outdir,
                "--report-dir",
                self.outdir,
            ],
            stderr=subprocess.PIPE,
            shell=False,
        )

        if wait_for_start:
            # Read twister output until test case was started
            while True:
                line = self._readline()
                if line is None:
                    return

                if "OUTPUT: START - " in line:
                    log.info("Zephyr test started.")
                    return

    def _readline(self) -> Optional[str]:
        """Read a line from the twister process

        :return: The line as string or None if the process has finished
        """
        while True:
            read = self.process.stderr.readline()
            if len(read) == 0:
                return None
            line = read.decode("utf-8").strip()
            # Twister outputs many lines without a real message, remove those
            test_output_pattern = "- OUTPUT:"
            output = line.find(test_output_pattern)
            if output != -1:
                message = line[output + len(test_output_pattern) :]
                if len(message) == 0 or message.isspace():
                    continue
            log.debug(f"Twister: {line}")
            return line

    def _parse_xunit(self, file_path: str) -> TestResult:
        """Parse xunit file

        :return: The result of a single test case
        """
        tree = ET.parse(file_path)
        testcase = tree.getroot().find("testsuite").find("testcase")
        failure = testcase.find("failure")
        skipped = testcase.find("skipped")
        error = testcase.find("error")
        if failure is not None:
            log.info(f"Zephyr test failed: {failure.text}")
            result = TestResult.FAILED
        elif skipped is not None:
            log.info("Zephyr test was skipped.")
            result = TestResult.SKIPPED
        elif error is not None:
            log.error(f"Zephyr test failed with error: {error.text}")
            result = TestResult.ERROR
        else:
            log.info("Zephyr test PASSED.")
            result = TestResult.PASSED
        return result

    def wait_test(self) -> TestResult:
        """Wait for the test to finish

        :return: The result of the test, taken from the xunit output of twister
        """
        if self.process is None:
            raise ZephyrError("Twister was not started so can not wait.")

        # Read the twister console for log output
        while True:
            line = self._readline()
            if line is None:
                break

            # Look for relevant messages in the output
            if "OUTPUT:  PASS - " in line:
                log.debug("Zephyr test PASSED.")
            elif "OUTPUT:  FAIL - " in line:
                log.debug("Zephyr test FAILED.")

        # Wait for the twister process to exit
        self.process.wait()

        # Get the result from the xunit file
        result = self._parse_xunit(str(Path(self.outdir) / "twister_report.xml"))

        self.process = None
        return result


class ZephyrTestAuxiliary(SimpleAuxiliaryInterface):
    """Auxiliary for Zephyr test interaction using the twister commandline tool

    The functionality includes test case execution and result collection.

    """

    def __init__(
        self,
        com: CChannel = None,
        twister_path: str = "twister",
        test_directory: Optional[str] = None,
        test_name: Optional[str] = None,
        wait_for_start: bool = True,
        **kwargs,
    ) -> None:
        """Initialize the auxiliary

        :param twister_path: Path to the twister tool
        :param test_directory: The directory to search for the Zephyr test project
        :param testcase_name: The name of the Zephyr test
        :param wait_for_start: Wait for Zyephyr test start

        """
        self.twister_path = twister_path
        self.test_directory = test_directory
        self.test_name = test_name
        self.wait_for_start = wait_for_start
        self.twister = Twister(twister_path)
        super().__init__(**kwargs)

    def start_test(
        self, test_directory: Optional[str] = None, test_name: Optional[str] = None
    ) -> None:
        """Start the Zephyr test

        :param test_directory: The directory to search for the Zephyr test project. Defaults to the test_directory from YAML.
        :param testcase_name: The name of the Zephyr test. Defaults to the testcase_name from YAML.
        """
        test_directory = (
            test_directory if test_directory is not None else self.test_directory
        )
        test_name = test_name if test_name is not None else self.test_name
        if test_directory is None:
            raise ZephyrError("test_directory parameter is not set.")
        if test_name is None:
            raise ZephyrError("test_name parameter is not set.")
        self.twister.start_test(test_directory, test_name, self.wait_for_start)

    def wait_test(self) -> TestResult:
        return self.twister.wait_test()

    def _create_auxiliary_instance(self) -> bool:
        return True

    def _delete_auxiliary_instance(self) -> bool:
        return True


class NewZephyrTestAuxiliary(DTAuxiliaryInterface):
    """Auxiliary used to send raw bytes via a connector instead of pykiso.Messages."""

    def __init__(
        self,
        com: CChannel,
        twister_path: str = "twister",
        test_directory: Optional[str] = None,
        test_name: Optional[str] = None,
        wait_for_start: bool = True,
        **kwargs: dict,
    ) -> None:
        """Initialize the auxiliary

        :param com: CChannel for twister communication
        :param twister_path: Path to the twister tool
        :param test_directory: The directory to search for the Zephyr test project
        :param testcase_name: The name of the Zephyr test
        :param wait_for_start: Wait for Zyephyr test start

        """
        self.twister_path = twister_path
        self.test_directory = test_directory
        self.test_name = test_name
        self.wait_for_start = wait_for_start
        self.twister = Twister(twister_path)
        self.channel = com
        super().__init__(
            is_proxy_capable=True, tx_task_on=True, rx_task_on=True, **kwargs
        )

    def start_test(
        self, test_directory: Optional[str] = None, test_name: Optional[str] = None
    ) -> None:
        """Start the Zephyr test

        :param test_directory: The directory to search for the Zephyr test project. Defaults to the test_directory from YAML.
        :param testcase_name: The name of the Zephyr test. Defaults to the testcase_name from YAML.
        """
        test_directory = (
            test_directory if test_directory is not None else self.test_directory
        )
        test_name = test_name if test_name is not None else self.test_name
        if test_directory is None:
            raise ZephyrError("test_directory parameter is not set.")
        if test_name is None:
            raise ZephyrError("test_name parameter is not set.")
        self.twister.start_test(test_directory, test_name, self.wait_for_start)

    def wait_test(self) -> TestResult:
        return self.twister.wait_test()

    @open_connector
    def _create_auxiliary_instance(self) -> bool:
        """Open the connector communication.

        :return: True if the channel is correctly opened otherwise False
        """
        log.internal_info("Auxiliary instance created")
        return True

    @close_connector
    def _delete_auxiliary_instance(self) -> bool:
        """Close the connector communication.

        :return: always True
        """
        log.internal_info("Auxiliary instance deleted")
        return True

    def send_message(self, raw_msg: bytes) -> bool:
        """Send a raw message (bytes) via the communication channel.

        :param raw_msg: message to send

        :return: True if command was executed otherwise False
        """
        return self.run_command("send", raw_msg)

    def run_command(
        self,
        cmd_message,
        cmd_data=None,
        blocking: bool = True,
        timeout_in_s: int = None,
    ) -> bool:
        """Send a request by transmitting it through queue_in and
        populate queue_tx with the command verdict (successful or not).

        :param cmd_message: command to send
        :param cmd_data: data you would like to populate the command
            with
        :param blocking: If you want the command request to be
            blocking or not
        :param timeout_in_s: Number of time (in s) you want to wait
            for an answer

        :return: True if the request is correctly executed otherwise
            False
        """
        with self.lock:
            log.internal_debug(
                f"sending command '{cmd_message}' with payload {cmd_data} using {self.name} aux."
            )
            state = None
            self.queue_in.put((cmd_message, cmd_data))
            try:
                state = self.queue_tx.get(blocking, timeout_in_s)
                log.internal_debug(
                    f"command '{cmd_message}' successfully sent for {self.name} aux"
                )
            except queue.Empty:
                log.error(
                    f"no feedback received regarding request {cmd_message} for {self.name} aux."
                )
        return state

    def receive_message(
        self,
        blocking: bool = True,
        timeout_in_s: float = None,
    ) -> Optional[bytes]:
        """Receive a raw message.

        :param blocking: wait for message till timeout elapses?
        :param timeout_in_s: maximum time in second to wait for a response

        :returns: raw message
        """

        # Evaluate if we are in the context manager or not
        in_ctx_manager = False
        if self.queueing_event.is_set():
            in_ctx_manager = True

        log.internal_debug(
            f"retrieving message in {self} (blocking={blocking}, timeout={timeout_in_s})"
        )
        # In case we are not in the context manager, we have a enable the receiver thread (and afterwards disable it)
        if not in_ctx_manager:
            self.queueing_event.set()
        response = self.wait_for_queue_out(blocking=blocking, timeout_in_s=timeout_in_s)
        if not in_ctx_manager:
            self.queueing_event.clear()

        log.internal_debug(f"retrieved message '{response}' in {self}")

        # if queue.Empty exception is raised None is returned so just
        # directly return it
        if response is None:
            return None

        msg = response.get("msg")
        remote_id = response.get("remote_id")

        # stay with the old return type to not making a breaking change
        if remote_id is not None:
            return (msg, remote_id)
        return msg

    def clear_buffer(self) -> None:
        """Clear buffer from old stacked objects"""
        log.internal_info("Clearing buffer. Previous responses will be deleted.")
        with self.queue_out.mutex:
            self.queue_out.queue.clear()

    def _run_command(self, cmd_message: str, cmd_data: bytes = None) -> bool:
        """Run the corresponding command.

        :param cmd_message: command type
        :param cmd_data: payload data to send over CChannel

        :return: True if command is executed otherwise False
        """
        state = False
        if cmd_message == "send":
            try:
                self.channel.cc_send(msg=cmd_data, raw=True)
                state = True
            except Exception:
                log.exception(
                    f"encountered error while sending message '{cmd_data}' to {self.channel}"
                )
        else:
            log.internal_warning(f"received unknown command '{cmd_message} in {self}'")

        self.queue_tx.put(state)

    def _receive_message(self, timeout_in_s: float) -> None:
        """Get a message from the associated channel. And put the message in
        the queue, if threading event is set.

        :param timeout_in_s: maximum amount of time (seconds) to wait
            for a message
        """
        try:
            rcv_data = self.channel.cc_receive(timeout=timeout_in_s, raw=True)
            log.internal_debug(f"received message '{rcv_data}' from {self.channel}")
            msg = rcv_data.get("msg")
            if msg is not None and self.queueing_event.is_set():
                self.queue_out.put(rcv_data)
        except Exception:
            log.exception(
                f"encountered error while receiving message via {self.channel}"
            )
