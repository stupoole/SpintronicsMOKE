import logging

import nidaqmx as nidaq
import numpy as np
from nidaqmx.stream_writers import DigitalSingleChannelWriter
import time
import sys

from WrapperClasses import CameraGrabber, LampController


class AnalyserController:
    def __init__(self, reset=False):
        """
        :param bool reset: Choose whether to reset the DAQ card or not. Because DAQ based controllers are
        all using the same DAQ device, this should only be true for the first of these two objects to be created.
        """
        logging.info("Initialising AnalyserController")

        try:
            self.dev = nidaq.system.device.Device('Dev1')
        except:
            logging.error("Failed to connect to DAQ card. Is it on?")
            sys.exit(-1)

        self.FINE = 8
        self.DIR = 2
        self.CLOCK = 4
        self.ENABLE = 1
        self.STEPS_PER_DEGREE = 222
        self.position_in_steps = 0
        self.position_in_degrees = 0

        if reset:
            logging.info("Resetting DAQ card")
            self.dev.reset_device()

        self.stepper_task = nidaq.Task()

        self.stepper_task.do_channels.add_do_chan('Dev1/port2/line0:3')
        self.stepper_stream = DigitalSingleChannelWriter(self.stepper_task.out_stream, True)

    def _step_forward(self, steps: int, fine=False):
        """
        Moves the analyser by the specified number of steps in the arbitrarily defined forward direction
        :param steps: Number of steps to move
        :param fine: If enabled, the steps are smaller by a factor of 8.
        :return None:
        """
        logging.debug(f"moving {steps} steps")
        data = [self.FINE * fine + self.CLOCK, self.FINE * fine]
        for i in range(steps):
            self.stepper_stream.write_one_sample_port_byte(data[0])
            time.sleep(2e-3)
            self.stepper_stream.write_one_sample_port_byte(data[1])
            time.sleep(2e-3)
            if fine:
                self.position_in_steps += 1/8
                self.position_in_degrees += (1 / 8) / self.STEPS_PER_DEGREE
            else:
                self.position_in_steps += 1
                self.position_in_degrees += 1 / self.STEPS_PER_DEGREE

    def _step_backward(self, steps: int, fine=False):
        """
        Moves the analyser by the specified number of steps in the arbitrarily defined backward direction
        :param steps: Number of steps to move
        :param fine: If enabled, the steps are smaller by a factor of 8.
        :return None:
        """
        logging.debug(f"moving -{steps} steps")
        data = [self.FINE * fine + self.CLOCK + self.DIR, self.FINE * fine + self.DIR]
        for i in range(steps):
            self.stepper_stream.write_one_sample_port_byte(data[0])
            time.sleep(2e-3)
            self.stepper_stream.write_one_sample_port_byte(data[1])
            time.sleep(2e-3)
            if fine:
                self.position_in_steps -= 1/8
                self.position_in_degrees -= (1 / 8) / self.STEPS_PER_DEGREE
            else:
                self.position_in_steps -= 1
                self.position_in_degrees -= 1 / self.STEPS_PER_DEGREE

    def move(self, degrees: float, force_fine=False):
        """
        Rotate the polariser a number of degrees (positive or negative). Rate of movement is approx 1 degree per second.
        :param force_fine: force the fine mode even for movements larger than fine movements.
        :param degrees: number of degrees to rotate
        :return None:
        """
        if abs(degrees) <= 1 / (8 * self.STEPS_PER_DEGREE):
            logging.warning(f"Ignored attempted to move. Reason: Number steps smaller than 1")
            return

        if abs(degrees) <= 1 / self.STEPS_PER_DEGREE or force_fine:
            logging.info(f"Using fine mode to move {degrees} degrees")
            fine = True
            steps = int(abs(degrees) * self.STEPS_PER_DEGREE * 8)
        else:
            fine = False
            steps = int(abs(degrees) * self.STEPS_PER_DEGREE)

        if degrees > 0:
            self._step_forward(steps, fine)
        else:
            self._step_backward(steps, fine)

    def find_minimum(self, _camera_grabber, roi=None):
        """
        Moves the analyser to the position of minimum intensity. Must have a single pair of LEDs on in order to use this.
        The camera should be finished and acquisition should be stopped. LED flickering must be off.
        :param CameraGrabber _camera_grabber: CameraGrabber object, needed for getting frame intensities.
        :return None:
        """
        _camera_grabber.cam.set_attribute_value("EXPOSURE TIME", 3e-3)
        _camera_grabber.prepare_camera()
        intensities = []
        positions = []
        if roi:
            x, y, w, h = roi
        # First pick a direction which will result in a decrease in intensity
        while True:
            frame = _camera_grabber.snap()
            if roi:
                intensity = np.mean(frame[y:y + h, x:x + w], axis=(0, 1))
            else:
                intensity = np.mean(frame, axis=(0, 1))
            intensities.append(intensity)
            positions.append(self.position_in_degrees)
            logging.debug(f"intensity: {intensity} at position {self.position_in_degrees} deg")

            self.move(1)

            frame = _camera_grabber.snap()
            intensity = np.mean(frame, axis=(0, 1))
            intensities.append(intensity)
            positions.append(self.position_in_degrees)
            change = intensities[-1] - intensities[-2]
            if abs(change) <= 0.01:
                # Catch the case where it is as a maximum so can't determine which direction to move.
                if intensities[-1] >= 65534:
                    logging.warning("No changes detected because the camera is saturated. Moving again.")
                    self.move(1)
                else:
                    logging.warning("No changes detected and the camera is not saturated. Already at minimum?")
                    return
            else:
                # If moving increased the value, move the other way to find the minimum.
                if change > 0:
                    move_degrees = -0.1
                    logging.info(f"Moving forward to find minimum")
                else:
                    move_degrees = 0.1
                    logging.info(f"Moving backward to find minimum")
                break

        searching = True
        while searching:
            self.move(move_degrees)
            frame = _camera_grabber.snap()
            intensity = np.mean(frame, axis=(0, 1))
            intensities.append(intensity)
            logging.debug(f"intensity: {intensity} at position {self.position_in_degrees} deg")
            positions.append(self.position_in_degrees)
            if intensities[-1] - intensities[-2] > 0:
                # This should only occur when going past the minimum so the analyser will return by half a step to
                # estimate the minimum position. This could be done using interpolation
                self.move(-0.5 * move_degrees)
                searching = False
        logging.info(f"Moved {self.position_in_degrees} degrees to find the minimum.")
        self.position_in_degrees = 0
        self.position_in_steps = 0

    def close(self, reset=False):
        logging.info("Closing LampController")
        self.stepper_task.close()
        if reset:
            self.dev.reset_device()


if __name__ == "__main__":
    logging.root.setLevel(logging.NOTSET)
    lamp_controller = LampController(True)
    lamp_controller.enable_left_pair()
    controller = AnalyserController()
    # controller.move(5)
    camera_grabber = CameraGrabber(None)
    controller.find_minimum(camera_grabber)
    camera_grabber.cam.close()
    controller.close()
    lamp_controller.close(True)
