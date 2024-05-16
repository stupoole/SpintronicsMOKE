import sys
from collections import deque
from datetime import datetime
from pathlib import Path

import pandas as pd
import tifffile
from PyQt5 import uic
import cv2

import pyqtgraph as pg

from WrapperClasses import *

import os.path
from os import listdir
from os.path import isfile, join
import sys

import numpy as np
from PyQt5 import QtCore, QtWidgets, uic, QtGui

import logging
from logging.handlers import RotatingFileHandler

pg.setConfigOption('background', 'w')
pg.setConfigOption('foreground', 'k')


class ArtieLabUI(QtWidgets.QMainWindow):
    def __init__(self):
        # Loads the UI file and sets it to full screen
        super(ArtieLabUI, self).__init__()
        uic.loadUi('res/ArtieLab.ui', self)
        self.__prepare_logging()
        right_monitor = QtWidgets.QDesktopWidget().screenGeometry(1)
        self.move(right_monitor.left(), right_monitor.top())

        # Define variables
        self.mutex = QtCore.QMutex()
        self.binning = 2
        self.BUFFER_SIZE = 2
        self.frame_buffer = deque(maxlen=self.BUFFER_SIZE)
        self.item_semaphore = QtCore.QSemaphore(0)
        self.spaces_semaphore = QtCore.QSemaphore(self.BUFFER_SIZE)
        self.plot_timer = QtCore.QTimer(self)
        self.magnetic_field_timer = QtCore.QTimer(self)
        self.image_timer = QtCore.QTimer(self)

        self.enabled_leds_spi = {
            "left1": False,
            "left2": False,
            "right1": False,
            "right2": False,
            "up1": False,
            "up2": False,
            "down1": False,
            "down2": False
        }

        self.LED_brightnesses = {
            "left1": 180,
            "left2": 180,
            "right1": 180,
            "right2": 180,
            "up1": 180,
            "up2": 180,
            "down1": 180,
            "down2": 180
        }

        self.enabled_led_pairs = {
            "left": False,
            "right": False,
            "up": False,
            "down": False
        }

        self.led_binary_enum = {
            "left1": 2,
            "left2": 1,
            "right1": 8,
            "right2": 4,
            "up1": 32,
            "up2": 16,
            "down1": 128,
            "down2": 64
        }

        self.led_id_enum = {
            "left1": 2,
            "left2": 1,
            "right1": 4,
            "right2": 3,
            "up1": 6,
            "up2": 5,
            "down1": 8,
            "down2": 7
        }

        # Create controller objects and threads
        self.lamp_controller = LampController(reset=True)
        self.magnet_controller = MagnetController()
        self.analyser_controller = AnalyserController()
        self.lamp_controller.disable_all()

        self.frame_processor = FrameProcessor(self)
        self.frame_processor_thread = QtCore.QThread()
        self.frame_processor.moveToThread(self.frame_processor_thread)
        self.frame_processor_thread.start()

        self.camera_grabber = CameraGrabber(self)
        self.camera_thread = QtCore.QThread()
        self.camera_grabber.moveToThread(self.camera_thread)
        self.camera_thread.start()

        self.height, self.width = self.camera_grabber.get_data_dims()

        # Program flow control
        self.flickering = False
        self.paused = False
        self.close_event = None
        self.get_background = False
        self.LED_control_all = False
        self.exposure_time = 0.05
        self.roi = (0, 0, 0, 0)

        # Magnetic field calibration stuff
        if os.path.isfile('res/last_calibration_location.txt'):
            with open('res/last_calibration_location.txt', 'r') as file:
                self.calib_file_dir = file.readline()
                logging.info("Previous calibration file directory found.")
        elif os.path.isdir("Coil Calibrations\\"):
            self.calib_file_dir = "Coil Calibrations\\"
            logging.warning("No calibration location found, trying: " + str(self.calib_file_dir))
        else:
            logging.warning("Default calib file location not found. Asking for user input.")
            self.calib_file_dir = QtWidgets.QFileDialog.getExistingDirectory(
                None,
                'Choose Calibration File Directory',
                QtWidgets.QFileDialog.ShowDirsOnly
            )

        self.__populate_calibration_combobox(self.calib_file_dir)

        self.__connect_signals()
        self.__prepare_views()

        # Actually display the window
        self.showMaximized()
        self.show()
        self.activateWindow()

        # Start image acquisition and update loops
        QtCore.QMetaObject.invokeMethod(self.camera_grabber, "start_live_single_frame",
                                        QtCore.Qt.ConnectionType.QueuedConnection)
        QtCore.QMetaObject.invokeMethod(self.frame_processor, "start_processing",
                                        QtCore.Qt.ConnectionType.QueuedConnection)
        self.start_time = time.time()
        self.image_timer.start(0)
        self.plot_timer.start(50)
        self.magnetic_field_timer.start(10)

    def __connect_signals(self):
        # LED controls
        self.button_left_led1.clicked.connect(self.__on_individual_led)
        self.button_right_led1.clicked.connect(self.__on_individual_led)
        self.button_up_led1.clicked.connect(self.__on_individual_led)
        self.button_down_led1.clicked.connect(self.__on_individual_led)
        self.button_left_led2.clicked.connect(self.__on_individual_led)
        self.button_right_led2.clicked.connect(self.__on_individual_led)
        self.button_up_led2.clicked.connect(self.__on_individual_led)
        self.button_down_led2.clicked.connect(self.__on_individual_led)
        self.button_leds_off.clicked.connect(self.__disable_all_leds)
        # LED Modes
        self.button_long_pol.clicked.connect(self.__on_long_pol)
        self.button_trans_pol.clicked.connect(self.__on_trans_pol)
        self.button_polar.clicked.connect(self.__on_polar)
        self.button_long_trans.clicked.connect(self.__on_long_trans)
        self.button_pure_long.clicked.connect(self.__on_pure_long)
        self.button_pure_trans.clicked.connect(self.__on_pure_trans)
        # LED Brightness
        self.button_LED_control_all.clicked.connect(self.__on_control_change)
        self.button_LED_reset_all.clicked.connect(self.__reset_brightness)
        self.scroll_LED_brightness.valueChanged.connect(self.__on_brightness_slider)
        self.scroll_blocker = QtCore.QSignalBlocker(self.scroll_LED_brightness)
        self.scroll_blocker.unblock()

        # Image Processing Controls
        self.combo_normalisation_selector.currentIndexChanged.connect(self.__on_image_processing_mode_change)
        self.spin_percentile_lower.valueChanged.connect(self.__on_image_processing_spin_box_change)
        self.spin_percentile_upper.valueChanged.connect(self.__on_image_processing_spin_box_change)
        self.spin_clip.valueChanged.connect(self.__on_image_processing_spin_box_change)
        self.button_ROI_select.clicked.connect(self.__select_roi)
        self.button_draw_line.clicked.connect(self.__draw_line)
        self.button_flip_line.clicked.connect(self.__on_flip_line)
        self.button_clear_roi.clicked.connect(self.__on_clear_roi)
        self.button_clear_line.clicked.connect(self.__on_clear_line)
        self.frame_processor.frame_processor_ready.connect(self.__on_frame_processor_ready)

        # Averaging controls
        self.button_measure_background.clicked.connect(self.__on_get_new_background)
        self.button_toggle_averaging.clicked.connect(self.__on_averaging)
        self.spin_foreground_averages.valueChanged.connect(self.__on_average_changed)

        # Camera Controls
        # self.combo_targetfps.currentIndexChanged.connect(self.__on_exposure_time_changed)
        self.spin_exposure_time.editingFinished.connect(self.__on_exposure_time_changed)
        self.combo_binning.currentIndexChanged.connect(self.__on_binning_mode_changed)
        self.button_pause_camera.clicked.connect(self.__on_pause_button)
        self.button_display_subtraction.clicked.connect(self.__on_show_subtraction)

        # Data Streams and Signals
        self.camera_grabber.camera_ready.connect(self.__on_camera_ready)
        self.camera_grabber.quit_ready.connect(self.__on_quit_ready)

        # saving GUI
        self.button_save_package.clicked.connect(self.__on_save)
        self.button_save_single.clicked.connect(self.__on_save_single)
        self.button_dir_browse.clicked.connect(self.__on_browse)

        # Magnetic Field Control

        self.combo_calib_file.currentIndexChanged.connect(self.__on_change_calibration)
        self.spin_mag_amplitude.valueChanged.connect(self.__on_change_field_amplitude)
        self.spin_mag_offset.valueChanged.connect(self.__on_change_field_offset)
        self.spin_mag_freq.valueChanged.connect(self.__on_change_mag_freq)

        self.button_zero_field.clicked.connect(self.__set_zero_field)
        self.button_DC_field.clicked.connect(self.__on_DC_field)
        self.button_AC_field.clicked.connect(self.__on_AC_field)
        self.button_invert_field.clicked.connect(self.__on_invert_field)

        # Analyser Controls
        self.button_move_analyser_back.clicked.connect(self.__rotate_analyser_backward)
        self.button_move_analyser_for.clicked.connect(self.__rotate_analyser_forward)

        # Plot controls
        self.magnetic_field_timer.timeout.connect(self.__update_field_measurement)
        self.plot_timer.timeout.connect(self.__update_plots)
        self.image_timer.timeout.connect(self.__update_images)
        self.spin_number_of_points.valueChanged.connect(self.__on_change_plot_count)
        self.spin_mag_point_count.valueChanged.connect(self.__on_change_mag_plot_count)
        self.button_reset_plots.clicked.connect(self.__on_reset_plots)

        # TODO: Add Draw Line feature

    def __prepare_logging(self):
        self.log_text_box = HTMLBasedColorLogger(self)

        # self.log_text_box.setFormatter(
        # logging.Formatter('%(asctime)s %(levelname)s %(module)s - %(message)s', "%H:%M:%S"))
        # logging.Formatter(CustomLoggingFormatter())
        logging.getLogger().addHandler(self.log_text_box)
        self.log_text_box.setFormatter(CustomLoggingFormatter())
        logging.getLogger().setLevel(logging.INFO)
        self.layout_logging.addWidget(self.log_text_box.widget)

        fh = RotatingFileHandler('ArtieLabUI.log',
                                 mode='a',
                                 maxBytes=1024 * 1024,
                                 backupCount=1,
                                 encoding=None,
                                 delay=False,
                                 errors=None
                                 )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(
            logging.Formatter(
                '%(asctime)s %(levelname)s %(module)s %(funcName)s %(message)s'))
        logging.getLogger().addHandler(fh)

    def __prepare_views(self):
        self.stream_window = 'HamamatsuView'
        window_width = self.width
        window_height = self.height
        cv2.namedWindow(
            self.stream_window,
            flags=(cv2.WINDOW_NORMAL | cv2.WINDOW_GUI_NORMAL | cv2.WINDOW_FREERATIO))
        cv2.setWindowProperty(self.stream_window, cv2.WND_PROP_TOPMOST, 1.0)
        cv2.setWindowProperty(self.stream_window, cv2.WND_PROP_FULLSCREEN, 1.0)
        cv2.resizeWindow(
            self.stream_window,
            window_width,
            window_height)
        cv2.moveWindow(
            self.stream_window,
            0,
            0)

        self.plots_canvas = pg.GraphicsLayoutWidget()
        self.layout_plots.addWidget(self.plots_canvas)

        self.intensity_plot = self.plots_canvas.addPlot(
            row=0,
            col=0,
            title="Intensity",
            left="mean intensity",
            bottom="time (s)"
        )
        self.intensity_line = self.intensity_plot.plot(list(self.frame_processor.frame_times),
                                                       list(self.frame_processor.intensities_y), pen="k")
        self.hist_plot = self.plots_canvas.addPlot(
            row=1,
            col=0,
            title="Histogram as seen",
            left="counts",
            bottom="intensity"
        )
        self.hist_line = self.hist_plot.plot(self.frame_processor.latest_hist_bins,
                                             self.frame_processor.latest_hist_data, pen="k")
        self.roi_plot = self.plots_canvas.addPlot(
            row=2,
            col=0,
            title="ROI Intensity",
            left="mean intensity",
            bottom="time (s)"
        )
        self.roi_line = self.roi_plot.plot([], pen="k")
        self.roi_plot.hide()

        self.line_profile_plot = self.plots_canvas.addPlot(
            row=3,
            col=0,
            title="Line Profile",
            left="intensity",
            bottom="pixel index"
        )
        self.line_profile_line = self.line_profile_plot.plot([], [], pen="k")
        self.line_profile_plot.hide()

        self.mag_plot_canvas = pg.GraphicsLayoutWidget()
        self.layout_mag_plot.addWidget(self.mag_plot_canvas)

        self.mag_plot = self.mag_plot_canvas.addPlot(
            row=0,
            col=0,
            title="Magnetic Field",
            left="Field (mT)",
            bottom="time (s)"
        )
        self.mag_y = deque(maxlen=100)
        self.mag_t = deque(maxlen=100)
        self.mag_line = self.mag_plot.plot(self.mag_t, self.mag_y, pen="k")

    def __populate_calibration_combobox(self, dir):

        file_names = [f for f in listdir(dir) if isfile(join(dir, f)) and ".txt" in f]
        if file_names:
            self.calibration_dictionary = {i + 1: name for i, name in enumerate(file_names)}
            # +1 because 0 is "None"

            strings = [name.replace('.txt', '') for name in file_names]
            strings = [name.replace('_fit', '') for name in strings]
            self.combo_calib_file.clear()
            self.combo_calib_file.addItem("None")
            self.combo_calib_file.addItems(strings)
        else:
            logging.warning(" No magnet calibration files found.")

    def __update_plots(self):
        # Sometimes, the update can be called between the appending to frame times and to intensities
        length = min([self.frame_processor.frame_times.__len__(), self.frame_processor.intensities_y.__len__()])
        if length > 0:
            self.intensity_line.setData(
                np.array(self.frame_processor.frame_times)[0:length] - np.min(self.frame_processor.frame_times),
                list(self.frame_processor.intensities_y)[0:length]
            )

        self.hist_line.setData(
            self.frame_processor.latest_hist_bins,
            self.frame_processor.latest_hist_data
        )

        # self.frame_time_line.setData(np.diff(self.frame_processor.frame_times))
        if len(self.frame_processor.frame_times) > 1:
            n_to_avg = min(10, self.frame_processor.frame_times.__len__())
            self.line_FPSdisplay.setText(
                "%.3f" % (1 / (np.mean(np.diff(np.array(self.frame_processor.frame_times)[-n_to_avg:]))))
            )

        length = min([self.frame_processor.frame_times.__len__(), self.frame_processor.roi_int_y.__len__()])
        if sum(self.frame_processor.roi) > 0 and length > 0:
            self.roi_line.setData(
                np.array(self.frame_processor.frame_times)[-length:] - np.min(self.frame_processor.frame_times),
                list(self.frame_processor.roi_int_y)[-length:]
            )

        if self.frame_processor.line_coords is not None and len(self.frame_processor.latest_profile) > 0:
            self.line_profile_line.setData(
                self.frame_processor.latest_profile
            )

        self.mag_line.setData(self.mag_t, self.mag_y)

        if self.frame_processor.averaging:
            if self.flickering:
                progress = (self.frame_processor.diff_frame_stack_a.shape[0] /
                            self.spin_foreground_averages.value() * 100)
            else:
                progress = (self.frame_processor.raw_frame_stack.shape[0] /
                            self.spin_foreground_averages.value() * 100)
            self.bar_averaging.setValue(int(progress))
        else:
            self.bar_averaging.setValue(0)

    def __update_images(self):
        frame = self.frame_processor.latest_processed_frame.astype(np.uint16)
        if sum(self.frame_processor.roi) > 0:
            x, y, w, h = self.frame_processor.roi
            frame = cv2.rectangle(
                frame,
                (x, y),
                (x + w, y + h),
                color=(0, 0, 0),
                thickness=2
            )
        if self.frame_processor.line_coords is not None:
            start, end = self.frame_processor.line_coords
            frame = cv2.arrowedLine(
                frame,
                start[::-1],
                end[::-1],
                color=(0, 0, 0),
                thickness=2
            )

        cv2.imshow(self.stream_window, frame)
        cv2.waitKey(1)

    def __update_field_measurement(self):
        field, voltage = self.magnet_controller.get_current_amplitude()
        self.line_measured_field.setText("{:0.4f}".format(field))
        self.line_measured_voltage.setText("{:0.4f}".format(voltage))
        self.mag_y.append(field)
        self.mag_t.append(time.time() - self.start_time)

    def __on_change_mag_plot_count(self, value):
        self.mag_y = deque(self.mag_y, maxlen=value)
        self.mag_t = deque(self.mag_t, maxlen=value)

    def __on_reset_plots(self):
        self.mutex.lock()
        self.frame_processor.frame_times = deque(maxlen=self.spin_number_of_points.value())
        self.frame_processor.intensities_y = deque(maxlen=self.spin_number_of_points.value())
        self.frame_processor.roi_int_y = deque(maxlen=self.spin_number_of_points.value())
        self.mutex.unlock()

    def __on_change_plot_count(self, value):
        self.mutex.lock()
        self.frame_processor.frame_times = deque(self.frame_processor.frame_times, maxlen=value)
        self.frame_processor.intensities_y = deque(self.frame_processor.intensities_y, maxlen=value)
        self.frame_processor.roi_int_y = deque(self.frame_processor.roi_int_y, maxlen=value)
        self.mutex.unlock()

    def __reset_pairs(self):
        self.enabled_led_pairs.update(
            {"left": False,
             "right": False,
             "up": False,
             "down": False})

    def __reset_led_spis(self):
        self.enabled_leds_spi.update(
            {"left1": False,
             "left2": False,
             "right1": False,
             "right2": False,
             "up1": False,
             "up2": False,
             "down1": False,
             "down2": False}
        )

    def __reset_brightness(self):
        self.LED_brightnesses.update(
            {"left1": 180,
             "left2": 180,
             "right1": 180,
             "right2": 180,
             "up1": 180,
             "up2": 180,
             "down1": 180,
             "down2": 180}
        )
        self.lamp_controller.set_all_brightness(180)

    def __get_lighting_configuration(self):
        if self.button_long_pol.isChecked():
            return "longitudinal and polar"
        elif self.button_trans_pol.isChecked():
            return "transpose and polar"
        elif self.button_polar.isChecked():
            return "polar"
        elif self.button_long_trans.isChecked():
            return "longitudinal and transpose and polar"
        elif self.button_pure_long.isChecked():
            return "pure longitudinal"
        elif self.button_pure_trans.isChecked():
            return "pure transpose"
        else:
            return [self.button_up_led1.isChecked(),
                    self.button_up_led2.isChecked(),
                    self.button_down_led1.isChecked(),
                    self.button_down_led2.isChecked(),
                    self.button_left_led1.isChecked(),
                    self.button_left_led2.isChecked(),
                    self.button_right_led1.isChecked(),
                    self.button_right_led2.isChecked()]

    def __on_image_processing_spin_box_change(self, ignored_event):
        self.frame_processor.set_percentile_lower(self.spin_percentile_lower.value())
        self.frame_processor.set_percentile_upper(self.spin_percentile_upper.value())
        self.frame_processor.set_clip_limit(self.spin_clip.value())

    def __on_image_processing_mode_change(self, mode):
        self.frame_processor.set_mode(mode)
        match mode:
            case -1:
                pass
            case 0:  # None
                self.spin_percentile_lower.setEnabled(False)
                self.spin_percentile_upper.setEnabled(False)
                self.spin_clip.setEnabled(False)
            case 1:  # Basic
                self.spin_percentile_lower.setEnabled(False)
                self.spin_percentile_upper.setEnabled(False)
                self.spin_clip.setEnabled(False)
            case 2:  # Contrast stretching
                self.spin_percentile_lower.setEnabled(True)
                self.spin_percentile_upper.setEnabled(True)
                self.spin_clip.setEnabled(False)

                self.frame_processor.set_percentile_lower(self.spin_percentile_lower.value())
                self.frame_processor.set_percentile_upper(self.spin_percentile_upper.value())
                # This is contrast stretching and needs min and max percentiles
            case 3:  # Histrogram eq
                self.spin_percentile_lower.setEnabled(False)
                self.spin_percentile_upper.setEnabled(False)
                self.spin_clip.setEnabled(False)
                # this is auto hist and so no other settings are needed
            case 4:  # Adaptive eq
                self.spin_percentile_lower.setEnabled(False)
                self.spin_percentile_upper.setEnabled(False)
                self.spin_clip.setEnabled(True)
                self.frame_processor.set_clip_limit(self.spin_clip.value())
                # this is Adaptive EQ and needs a clip limit

    def __select_roi(self):
        logging.log(
            ATTENTION_LEVEL,
            "Select a ROI and then press SPACE or ENTER button! \n"+
            "   Cancel the selection process by pressing c button")
        self.image_timer.stop()
        # Seleting using the raw frame means that the scaling is handled automatically.
        roi = cv2.selectROI(self.stream_window, self.frame_processor.latest_processed_frame.astype(np.uint16),
                            showCrosshair=True, printNotice=False)
        if sum(roi) > 0:
            # self.frame_processor.roi = tuple([int(value * (2 / self.binning)) for value in roi])
            self.frame_processor.roi = roi
            self.roi_plot.show()
            logging.info("ROI set to " + str(roi))
            self.button_clear_roi.setEnabled(True)
            logging.info(f'Binning mode: {self.binning}, roi: {self.frame_processor.roi}')
        else:
            logging.info('Failed to set ROI')
            self.__on_clear_roi()

        self.image_timer.start(0)

    def __draw_line(self):

        logging.log(
            ATTENTION_LEVEL,
            "Select a bounding box and then press SPACE or ENTER button! \n"+
            "   Cancel the selection process by pressing c button")
        self.image_timer.stop()

        roi = cv2.selectROI(self.stream_window, self.frame_processor.latest_processed_frame.astype(np.uint16),
                            showCrosshair=True, printNotice=False)

        if sum(roi) > 0:
            x, y, w, h = roi
            self.frame_processor.line_coords = ((y, x), (y + h, x + w))
            self.line_profile_plot.show()
            self.button_clear_line.setEnabled(True)
            self.button_flip_line.setEnabled(True)
            logging.info(
                f'Binning mode: {self.binning}, line between: {self.frame_processor.line_coords[0]}' +
                f' and {self.frame_processor.line_coords[1]}')
        else:
            logging.warning('Failed to set line profile')
            self.__on_clear_line()
        self.image_timer.start(0)
        self.button_clear_line.setEnabled(True)
        # cv2.line

    def __on_flip_line(self):
        (x1, y1), (x2, y2) = self.frame_processor.line_coords
        self.frame_processor.line_coords = ((x1, y2), (x2, y1))
        logging.info(
            f'Flipped line. Line now between: {self.frame_processor.line_coords[0]}' +
            f' and {self.frame_processor.line_coords[1]}')

    def __on_clear_roi(self):
        self.button_clear_roi.setEnabled(False)
        self.frame_processor.roi = (0, 0, 0, 0)
        self.frame_processor.roi_int_y = deque(maxlen=self.spin_number_of_points.value())
        self.roi_plot.hide()
        logging.info("Cleared ROI")

    def __on_clear_line(self):
        self.button_clear_line.setEnabled(False)
        self.button_flip_line.setEnabled(False)
        self.line_profile_plot.hide()
        self.frame_processor.line_coords = None
        logging.info("Cleared Line")

    def __disable_all_leds(self):
        logging.info("Disabling all LEDs")
        self.__reset_led_spis()
        self.__reset_pairs()
        if self.flickering:
            self.__reset_after_flicker_mode()

        self.button_long_pol.setChecked(False)
        self.button_trans_pol.setChecked(False)
        self.button_polar.setChecked(False)
        self.button_long_trans.setChecked(False)
        self.button_pure_long.setChecked(False)
        self.button_pure_trans.setChecked(False)

        self.button_up_led1.setChecked(False)
        self.button_up_led2.setChecked(False)
        self.button_down_led1.setChecked(False)
        self.button_down_led2.setChecked(False)
        self.button_left_led1.setChecked(False)
        self.button_left_led2.setChecked(False)
        self.button_right_led1.setChecked(False)
        self.button_right_led2.setChecked(False)

        self.__update_controller_pairs()

    def __on_individual_led(self, state):
        logging.info("Individual LED being called")
        if not self.flickering:
            if self.__check_for_any_active_LED_mode():
                self.button_long_pol.setChecked(False)
                self.button_trans_pol.setChecked(False)
                self.button_polar.setChecked(False)
                self.button_long_trans.setChecked(False)
                self.button_pure_long.setChecked(False)
                self.button_pure_trans.setChecked(False)
            self.enabled_leds_spi["up1"] = self.button_up_led1.isChecked()
            self.enabled_leds_spi["up2"] = self.button_up_led2.isChecked()
            self.enabled_leds_spi["down1"] = self.button_down_led1.isChecked()
            self.enabled_leds_spi["down2"] = self.button_down_led2.isChecked()
            self.enabled_leds_spi["left1"] = self.button_left_led1.isChecked()
            self.enabled_leds_spi["left2"] = self.button_left_led2.isChecked()
            self.enabled_leds_spi["right1"] = self.button_right_led1.isChecked()
            self.enabled_leds_spi["right2"] = self.button_right_led2.isChecked()
            self.__update_controller_spi()

    def __on_long_pol(self, checked):
        if checked:
            if self.flickering:
                self.__reset_after_flicker_mode()

            self.enabled_led_pairs.update(
                {"left": False,
                 "right": False,
                 "up": True,
                 "down": False})
            self.__reset_led_spis()

            self.button_up_led1.setChecked(True)
            self.button_up_led2.setChecked(True)
            self.button_down_led1.setChecked(False)
            self.button_down_led2.setChecked(False)
            self.button_left_led1.setChecked(False)
            self.button_left_led2.setChecked(False)
            self.button_right_led1.setChecked(False)
            self.button_right_led2.setChecked(False)

            self.button_trans_pol.setChecked(False)
            self.button_polar.setChecked(False)
            self.button_long_trans.setChecked(False)
            self.button_pure_long.setChecked(False)
            self.button_pure_trans.setChecked(False)

            self.__update_controller_pairs()
        else:
            if not self.__check_for_any_active_LED_mode():
                self.__disable_all_leds()

    def __on_trans_pol(self, checked):

        if checked:
            if self.flickering:
                self.__reset_after_flicker_mode()

            self.enabled_led_pairs.update({"left": True,
                                           "right": False,
                                           "up": False,
                                           "down": False})

            self.__reset_led_spis()

            self.button_up_led1.setChecked(False)
            self.button_up_led2.setChecked(False)
            self.button_down_led1.setChecked(False)
            self.button_down_led2.setChecked(False)
            self.button_left_led1.setChecked(True)
            self.button_left_led2.setChecked(True)
            self.button_right_led1.setChecked(False)
            self.button_right_led2.setChecked(False)

            self.button_long_pol.setChecked(False)
            self.button_polar.setChecked(False)
            self.button_long_trans.setChecked(False)
            self.button_pure_long.setChecked(False)
            self.button_pure_trans.setChecked(False)

            self.__update_controller_pairs()
        else:
            if not self.__check_for_any_active_LED_mode():
                self.__disable_all_leds()

    def __on_polar(self, checked):

        if checked:
            if self.flickering:
                self.__reset_after_flicker_mode()

            self.__reset_pairs()
            self.enabled_leds_spi.update(
                {"left1": False,
                 "left2": False,
                 "right1": False,
                 "right2": False,
                 "up1": True,
                 "up2": False,
                 "down1": True,
                 "down2": False})
            self.button_up_led1.setChecked(True)
            self.button_up_led2.setChecked(False)
            self.button_down_led1.setChecked(True)
            self.button_down_led2.setChecked(False)
            self.button_left_led1.setChecked(False)
            self.button_left_led2.setChecked(False)
            self.button_right_led1.setChecked(False)
            self.button_right_led2.setChecked(False)

            self.button_long_pol.setChecked(False)
            self.button_trans_pol.setChecked(False)
            self.button_long_trans.setChecked(False)
            self.button_pure_long.setChecked(False)
            self.button_pure_trans.setChecked(False)

            self.__update_controller_spi()
        else:
            if not self.__check_for_any_active_LED_mode():
                self.__disable_all_leds()

    def __on_long_trans(self, checked):
        if checked:
            if not self.flickering:
                self.__prepare_for_flicker_mode()
            else:
                self.lamp_controller.stop_flicker()

            self.button_long_pol.setChecked(False)
            self.button_trans_pol.setChecked(False)
            self.button_polar.setChecked(False)
            self.button_pure_long.setChecked(False)
            self.button_pure_trans.setChecked(False)

            self.button_up_led1.setChecked(True)
            self.button_up_led2.setChecked(True)
            self.button_left_led1.setChecked(True)
            self.button_left_led2.setChecked(True)
            self.button_down_led1.setChecked(False)
            self.button_down_led2.setChecked(False)
            self.button_right_led1.setChecked(False)
            self.button_right_led2.setChecked(False)

            self.lamp_controller.continuous_flicker(0, )
        else:
            if not self.__check_for_any_active_LED_mode():
                self.__disable_all_leds()

    def __on_pure_long(self, checked):
        if checked:
            if not self.flickering:
                self.__prepare_for_flicker_mode()
            else:
                self.lamp_controller.stop_flicker()

            self.button_long_pol.setChecked(False)
            self.button_trans_pol.setChecked(False)
            self.button_polar.setChecked(False)
            self.button_long_trans.setChecked(False)
            self.button_pure_trans.setChecked(False)

            self.lamp_controller.continuous_flicker(1)

            self.button_up_led1.setChecked(True)
            self.button_up_led2.setChecked(True)
            self.button_down_led1.setChecked(True)
            self.button_down_led2.setChecked(True)
            self.button_right_led1.setChecked(False)
            self.button_right_led2.setChecked(False)
            self.button_left_led1.setChecked(False)
            self.button_left_led2.setChecked(False)
        else:
            if not self.__check_for_any_active_LED_mode():
                self.__disable_all_leds()

    def __on_pure_trans(self, checked):
        if checked:
            if not self.flickering:
                self.__prepare_for_flicker_mode()
            else:
                self.lamp_controller.stop_flicker()

            self.button_long_pol.setChecked(False)
            self.button_trans_pol.setChecked(False)
            self.button_polar.setChecked(False)
            self.button_long_trans.setChecked(False)
            self.button_pure_long.setChecked(False)

            self.lamp_controller.continuous_flicker(2)

            self.button_up_led1.setChecked(True)
            self.button_up_led2.setChecked(True)
            self.button_right_led1.setChecked(True)
            self.button_right_led2.setChecked(True)
            self.button_down_led1.setChecked(False)
            self.button_down_led2.setChecked(False)
            self.button_left_led1.setChecked(False)
            self.button_left_led2.setChecked(False)
        else:
            if not self.__check_for_any_active_LED_mode():
                self.__disable_all_leds()

    def __prepare_for_flicker_mode(self):

        self.frame_processor.subtracting = False
        self.button_display_subtraction.setEnabled(False)
        # TODO: Add cv2 windows for raw frames

        self.flickering = True
        self.camera_grabber.running = False

        self.button_up_led1.setEnabled(False)
        self.button_up_led2.setEnabled(False)
        self.button_down_led1.setEnabled(False)
        self.button_down_led2.setEnabled(False)
        self.button_left_led1.setEnabled(False)
        self.button_left_led2.setEnabled(False)
        self.button_right_led1.setEnabled(False)
        self.button_right_led2.setEnabled(False)

        self.button_up_led1.setChecked(False)
        self.button_up_led2.setChecked(False)
        self.button_down_led1.setChecked(False)
        self.button_down_led2.setChecked(False)
        self.button_left_led1.setChecked(False)
        self.button_left_led2.setChecked(False)
        self.button_right_led1.setChecked(False)
        self.button_right_led2.setChecked(False)

    def __on_camera_ready(self):
        """
        Whenever the camera is stopped, this is automatically called but cannot be instigated until after the method
        that changed the running mode has been executed fully. If paused, this is ignored and unpausing manually
        restarts the camera.
        :return:
        """
        logging.info("ready received")
        if self.get_background:
            logging.info("Attempting to get background")
            self.get_background = False
            # Can't invoke method because of
            frames = self.camera_grabber.grab_n_frames(self.spin_background_averages.value())
            self.frame_processor.background_raw_stack = frames
            self.frame_processor.background = int_mean(frames, axis=0)
        if not self.paused:
            if self.flickering:
                QtCore.QMetaObject.invokeMethod(self.camera_grabber, "start_live_difference_mode",
                                                QtCore.Qt.ConnectionType.QueuedConnection)
                logging.info("Camera grabber starting difference mode")
            else:
                QtCore.QMetaObject.invokeMethod(self.camera_grabber, "start_live_single_frame",
                                                QtCore.Qt.ConnectionType.QueuedConnection)
                logging.info("Camera grabber starting normal mode")

    def __on_frame_processor_ready(self):
        logging.info("Frame processor ready received")
        self.mutex.lock()
        self.frame_processor.frame_counter = 0
        self.frame_processor.raw_frame_stack = np.array([], dtype=np.uint16).reshape(0, self.height, self.width)
        self.frame_processor.diff_frame_stack_a = np.array([], dtype=np.uint16).reshape(0, self.height, self.width)
        self.frame_processor.diff_frame_stack_b = np.array([], dtype=np.uint16).reshape(0, self.height, self.width)
        self.frame_processor.background = None
        self.frame_processor.background_raw_stack = None
        self.mutex.unlock()
        QtCore.QMetaObject.invokeMethod(self.frame_processor, "start_processing",
                                        QtCore.Qt.ConnectionType.QueuedConnection)

    def __reset_after_flicker_mode(self):
        logging.info("Resetting after flicker mode")
        self.item_semaphore = QtCore.QSemaphore(0)
        self.spaces_semaphore = QtCore.QSemaphore(self.BUFFER_SIZE)
        self.frame_buffer = deque(maxlen=self.BUFFER_SIZE)

        self.mutex.lock()
        self.flickering = False
        self.camera_grabber.running = False
        self.mutex.unlock()
        self.button_display_subtraction.setEnabled(True)
        self.frame_processor.subtracting = self.button_display_subtraction.isChecked()
        # TODO: hide the CV2 windows for the raw pos/neg

        self.button_up_led1.setEnabled(True)
        self.button_up_led2.setEnabled(True)
        self.button_down_led1.setEnabled(True)
        self.button_down_led2.setEnabled(True)
        self.button_left_led1.setEnabled(True)
        self.button_left_led2.setEnabled(True)
        self.button_right_led1.setEnabled(True)
        self.button_right_led2.setEnabled(True)
        self.lamp_controller.stop_flicker()

    def __check_for_any_active_LED_mode(self):
        return bool(
            self.button_long_pol.isChecked() +
            self.button_trans_pol.isChecked() +
            self.button_polar.isChecked() +
            self.button_long_trans.isChecked() +
            self.button_pure_long.isChecked() +
            self.button_pure_trans.isChecked()
        )

    def __get_magnet_mode(self):
        return (self.button_DC_field.isChecked() * 1 +
                self.button_AC_field.isChecked() * 2 +
                self.button_decay_field.isChecked() * 4)

    def __update_controller_pairs(self):
        self.lamp_controller.enable_assortment_pairs(self.enabled_led_pairs)

    def __update_controller_spi(self):
        # I assumed the numbering would go outside then inside, but it goes inside then outside
        value = self.enabled_leds_spi["left1"] * 2 \
                + self.enabled_leds_spi["left2"] * 1 \
                + self.enabled_leds_spi["right1"] * 8 \
                + self.enabled_leds_spi["right2"] * 4 \
                + self.enabled_leds_spi["up1"] * 32 \
                + self.enabled_leds_spi["up2"] * 16 \
                + self.enabled_leds_spi["down1"] * 128 \
                + self.enabled_leds_spi["down2"] * 64
        self.lamp_controller.enable_leds(value)

    def __on_control_change(self, control_all):
        self.LED_control_all = control_all
        if self.LED_control_all:
            self.button_LED_control_all.setText("Control\nSelected")
        else:
            self.button_LED_control_all.setText("Control\nAll")
        self.__update_brightness(180 - self.scroll_LED_brightness.value())

    def __on_brightness_slider(self, value):
        value = 180 - value
        logging.info(f"Slider Value Changed to: {value}")
        self.__update_brightness(value)

    def __update_brightness_slider(self):
        if self.LED_control_all:
            keys = self.LED_brightnesses.keys()
        else:
            keys = [key for key, value in self.enabled_leds_spi.items() if value is True]
        if keys:
            logging.debug("active LED keys: " + str(keys))
            brightest_val = max([self.LED_brightnesses[key] for key in keys])
            logging.debug(f"Brightest val: {brightest_val}")
            self.scroll_blocker.reblock()
            self.scroll_LED_brightness.setValue(180 - brightest_val)
            self.scroll_blocker.unblock()
            self.__update_brightness(brightest_val)

    def __update_brightness(self, value):
        if self.LED_control_all:
            keys = self.LED_brightnesses.keys()
        else:
            keys = [key for key, value in self.enabled_leds_spi.items() if value is True]
        if self.LED_control_all:
            self.LED_brightnesses = {key: value for key in self.LED_brightnesses}
            self.lamp_controller.set_all_brightness(value)
        else:
            for key in keys:
                self.LED_brightnesses[key] = value
            self.lamp_controller.set_some_brightness([value] * len(keys), [self.led_id_enum[key] for key in keys])

    def __on_get_new_background(self, ignored_event):
        logging.info("Getting background")
        self.get_background = True
        self.mutex.lock()
        self.camera_grabber.running = False
        self.mutex.unlock()

    def __on_exposure_time_changed(self):
        value = self.spin_exposure_time.value()
        if value != self.exposure_time:
            logging.info("Attempting to set exposure time to: %s", value)
            self.mutex.lock()
            self.camera_grabber.waiting = True
            self.camera_grabber.running = False
            self.mutex.unlock()
            self.camera_grabber.set_exposure_time(value)
            self.exposure_time = value

    def __on_binning_mode_changed(self, binning_idx):
        match binning_idx:
            case 0:
                value = 1
                dim = 2048
            case 1:
                value = 2
                dim = 1024
            case 2:
                value = 4
                dim = 512
            case _:
                logging.warning("Invalid Binning Mode!")
                return
        if value != self.binning:
            old_binning = self.binning
            self.binning = value
            logging.info(f"Attempting to set binning mode to {value}x{value}")
            self.mutex.lock()
            self.frame_processor.running = False
            self.camera_grabber.waiting = True
            self.camera_grabber.running = False
            self.mutex.unlock()
            self.camera_grabber.set_binning_mode(value)
            if sum(self.frame_processor.roi) > 0:
                self.frame_processor.roi = tuple(
                    [int(value * (old_binning / self.binning)) for value in self.frame_processor.roi])
                logging.info(f'Binning mode: {self.binning}, roi: {self.frame_processor.roi}')
            self.width = dim
            self.height = dim
            self.frame_processor.latest_processed_frame = np.zeros((dim, dim), dtype=np.uint16)

    def __on_average_changed(self, value):
        self.frame_processor.averages = value

    def __on_averaging(self, enabled):
        if enabled:
            self.button_toggle_averaging.setText("Disable Averaging (F3)")
            logging.info("Averaging enabled")
            self.mutex.lock()
            self.frame_processor.averaging = self.spin_foreground_averages.value()
            self.frame_processor.averaging = True

            self.frame_processor.frame_counter = 0
            self.frame_processor.raw_frame_stack = np.array([], dtype=np.uint16).reshape(0, self.height, self.width)
            self.frame_processor.diff_frame_stack_a = np.array([], dtype=np.uint16).reshape(0, self.height, self.width)
            self.frame_processor.diff_frame_stack_b = np.array([], dtype=np.uint16).reshape(0, self.height, self.width)
            self.mutex.unlock()
        else:
            self.button_toggle_averaging.setText("Enable Averaging (F3)")
            logging.info("Averaging disabled")
            self.mutex.lock()
            self.frame_processor.averaging = False
            self.mutex.unlock()

    def __on_show_subtraction(self, subtracting):
        if subtracting:
            self.button_display_subtraction.setText("Ignore Background (F2)")
            self.frame_processor.subtracting = True
        else:
            self.button_display_subtraction.setText("Show Subtraction (F2)")
            self.frame_processor.subtracting = False

    def __on_pause_button(self, paused):
        self.paused = paused
        if paused:
            self.mutex.lock()
            self.camera_grabber.running = False
            self.mutex.unlock()
            self.button_pause_camera.setText("Unpause (F4)")
            if self.flickering:
                self.lamp_controller.pause_flicker(paused)
        else:
            self.button_pause_camera.setText("Pause (F4)")
            if self.flickering:
                self.lamp_controller.pause_flicker(paused)
                QtCore.QMetaObject.invokeMethod(
                    self.camera_grabber,
                    "start_live_difference_mode",
                    QtCore.Qt.ConnectionType.QueuedConnection
                )
            else:
                QtCore.QMetaObject.invokeMethod(
                    self.camera_grabber,
                    "start_live_single_frame",
                    QtCore.Qt.ConnectionType.QueuedConnection
                )

    def __on_change_calibration(self, index):
        if index > 0:
            file_name = self.calibration_dictionary[index]
            calibration_array = np.loadtxt(os.path.join(self.calib_file_dir, file_name), delimiter=',', skiprows=1)
            logging.info("Setting calibration using file: " + str(file_name))
            self.magnet_controller.set_calibration(
                calibration_array[:, 0],
                calibration_array[:, 1],
                calibration_array[:, 2],
                calibration_array[:, 3]
            )
            max_field = np.amax(calibration_array[:, 1])
            self.label_amplitude.setText("Amplitude (mT)")
            self.label_offset.setText("Offset (mT)")
            self.label_measured_field.setText("Field (mT)")
            self.spin_mag_amplitude.setValue(0.0)
            self.spin_mag_amplitude.setRange(-max_field, max_field)
            self.spin_mag_amplitude.setSingleStep(round(max_field / 50, 1))
            self.spin_mag_offset.setValue(0.0)
            self.spin_mag_offset.setRange(-max_field, max_field)
            self.spin_mag_offset.setSingleStep(round(max_field / 50, 1))
        else:
            defaults = np.linspace(-10, 10, 100)
            self.magnet_controller.set_calibration(
                defaults,
                defaults,
                defaults,
                defaults
            )

    def __on_change_field_amplitude(self, value):
        if self.button_invert_field.isChecked():
            self.magnet_controller.set_target_field(-value)
        else:
            self.magnet_controller.set_target_field(value)

    def __on_change_field_offset(self, value):
        if self.button_invert_field.isChecked():
            self.magnet_controller.set_target_offset(-value)
        else:
            self.magnet_controller.set_target_offset(value)

    def __on_change_mag_freq(self, value):
        self.magnet_controller.set_frequency(value)

    def __set_zero_field(self):
        logging.info("Setting field output to zero")
        self.spin_mag_offset.setValue(0)
        self.spin_mag_amplitude.setValue(0)
        self.magnet_controller.reset_field()

    def __on_DC_field(self, enabled):
        if enabled:
            # self.button_DC_field.setChecked(True)
            self.button_AC_field.setChecked(False)
            if self.magnet_controller.mode == "AC":
                logging.info("Swapped from AC to DC field mode.")
            else:
                logging.info("Enabling DC field mode.")

            self.magnet_controller.mode = "DC"
        else:
            if not self.button_AC_field.isChecked():
                logging.warning("There is no field mode selected.")
                self.__set_zero_field()
                self.magnet_controller.mode = None
        self.magnet_controller.update_output()

    def __on_AC_field(self, enabled):
        if enabled:
            self.button_DC_field.setChecked(False)
            if self.magnet_controller.mode == "DC":
                logging.info("Swapped from DC to AC field mode.")
            else:
                logging.info("Enabling AC field mode.")
            self.magnet_controller.mode = "AC"
            self.magnet_controller.set_target_offset(self.spin_mag_offset.value())
            self.magnet_controller.set_frequency(self.spin_mag_freq.value())
        else:
            if not self.button_DC_field.isChecked():
                logging.warning("There is no field mode selected.")
                self.magnet_controller.mode = None
        self.magnet_controller.update_output()

    def __on_invert_field(self, inverted):
        if inverted:
            logging.info("Inverting field")
            self.magnet_controller.set_target_field(-self.spin_mag_amplitude.value())
            self.magnet_controller.set_target_offset(-self.spin_mag_offset.value())
        else:
            logging.info("Un-inverting field")
            self.magnet_controller.set_target_field(self.spin_mag_amplitude.value())
            self.magnet_controller.set_target_offset(self.spin_mag_offset.value())

    def __rotate_analyser_forward(self):
        amount = self.spin_analyser_move_amount.value()
        self.analyser_controller.move(amount)

    def __rotate_analyser_backward(self):
        amount = -self.spin_analyser_move_amount.value()
        self.analyser_controller.move(amount)

    def __on_save(self, event):
        meta_data = {
            'description': "Image acquired using B204 MOKE owned by the Spintronics Group and University of "
                           "Nottingham using ArtieLab V0-2024.04.05.",
            'camera': 'Hamamatsu C11440',
            'sample': self.line_prefix.text(),
            'lighting configuration': [self.__get_lighting_configuration()],
            'binnning': self.combo_binning.currentText(),
            'lens': self.combo_lens.currentText(),
            'magnification': self.combo_magnification.currentText(),
            'exposure_time': self.spin_exposure_time.value(),
            'correction': self.line_correction.text(),
            'time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        match self.__get_magnet_mode():
            case 0:
                meta_data['magnet_mode'] = None
            case 1:  # DC
                meta_data['magnet_mode'] = 'DC'
                meta_data['mag_field'] = self.mag_y[-1]
                meta_data['coil_calib'] = self.combo_calib_file.currentText()
            case 2:  # AC
                meta_data['magnet_mode'] = 'AC'
                meta_data['mag_field'] = self.mag_y[-1]
                meta_data['mag_field_amp'] = self.spin_mag_amplitude.value()
                meta_data['mag_field_freq'] = self.spin_mag_freq.value()
                meta_data['mag_field_offset'] = self.spin_mag_offset.value()
                meta_data['coil_calib'] = self.combo_calib_file.currentText()

        contents = []
        file_path = Path(self.line_directory.text()).joinpath(
            datetime.now().strftime("%Y-%m-%d--%H-%M-%S") + '_' + self.line_prefix.text().strip().replace(' ',
                                                                                                          '_') + '.h5')
        logging.info("Saving to: " + str(file_path) + ' This takes time. Please be patient.')
        try:
            store = pd.HDFStore(str(file_path))
        except:
            logging.info(
                "Cannot save to this file/location: " + file_path + '. Does it exist? Do you have write permissions?')
            return

        if self.flickering:
            if self.button_toggle_averaging.isChecked():
                if self.check_save_avg.isChecked():
                    key = 'mean_diff_frame'
                    contents.append(key)
                    store[key] = pd.DataFrame(self.frame_processor.latest_mean_diff)
                if self.check_save_stack.isChecked():
                    for i in range(self.frame_processor.diff_frame_stack_a.shape[0]):
                        key_a = 'raw_stack_a_' + str(i)
                        key_b = 'raw_stack_b_' + str(i)
                        contents.append(key_a)
                        contents.append(key_b)
                        store[key_a] = pd.DataFrame(self.frame_processor.diff_frame_stack_a[i])
                        store[key_b] = pd.DataFrame(self.frame_processor.diff_frame_stack_b[i])
            else:
                if self.check_save_avg.isChecked():
                    logging.info("Average not saved: measuring in single frame mode")
                if self.check_save_stack.isChecked():
                    logging.info("Stack not saved: measuring in single frame mode")
                key = 'raw_diff_frame'
                contents.append(key)
                store[key] = pd.DataFrame(self.frame_processor.latest_diff_frame)
                key = 'raw_frame_a'
                contents.append(key)
                store[key] = pd.DataFrame(self.frame_processor.latest_diff_frame_a)
                key = 'raw_frame_b'
                contents.append(key)
                store[key] = pd.DataFrame(self.frame_processor.latest_diff_frame_b)
        else:
            if self.button_toggle_averaging.isChecked():
                if self.check_save_avg.isChecked():
                    key = 'mean_frame'
                    contents.append(key)
                    store[key] = pd.DataFrame(self.frame_processor.latest_mean_frame)
                if self.check_save_stack.isChecked():
                    for i in range(self.frame_processor.raw_frame_stack.shape[0]):
                        key = 'raw_stack_' + str(i)
                        contents.append(key)
                        store[key] = pd.DataFrame(self.frame_processor.raw_frame_stack[i])
            else:
                if self.check_save_avg.isChecked():
                    logging.info("Average not saved: measuring in single frame mode")
                if self.check_save_stack.isChecked():
                    logging.info("Stack not saved: measuring in single frame mode")
                key = 'raw_frame'
                contents.append(key)
                store[key] = pd.DataFrame(self.frame_processor.latest_raw_frame)

        if self.check_save_as_seen.isChecked():
            key = 'as seen:'
            if self.button_toggle_averaging.isChecked():
                key += f' averaged({self.spin_foreground_averages.value()}) '
            if self.button_display_subtraction.isChecked() and not self.flickering:
                key += ' subtracted'
            if self.flickering:
                key += ' difference image'
            if key == 'as seen:':
                key += ' single frame'
            meta_data['normalisation'] = f'type: {self.combo_normalisation_selector.currentText()} ' + \
                                         f'lower: {self.spin_percentile_lower.value()} ' + \
                                         f'upper: {self.spin_percentile_upper.value()} ' + \
                                         f'clip: {self.spin_clip.value()}'
            contents.append(key)
            store[key] = pd.DataFrame(self.frame_processor.latest_processed_frame)

        if self.check_save_background.isChecked():
            if self.frame_processor.background is not None:
                key = 'background_avg'
                contents.append(key)
                store[key] = pd.DataFrame(self.frame_processor.background)
            else:
                logging.info("Background not saved: no background measured")
        if self.check_save_bkg_stack.isChecked():
            if self.frame_processor.background is not None:
                for i in range(len(self.frame_processor.background_raw_stack)):
                    key = 'bkg_stack_' + str(i)
                    contents.append(key)
                    store[key] = pd.DataFrame(self.frame_processor.background_raw_stack[i])
            else:
                logging.info("Background stack not saved: no background measured")
        meta_data['contents'] = [contents]
        store['meta_data'] = pd.DataFrame(meta_data)
        store.close()
        logging.info("Saving done. Contents: " + str(contents))

    def __on_save_single(self, event):
        # Assemble metadata
        meta_data = {
            'description': "Image acquired using B204 MOKE owned by the Spintronics Group and University of Nottingham using ArtieLab V0-2024.04.05.",
            'camera': 'Hamamatsu C11440',
            'sample': self.line_prefix.text(),
            'lighting configuration': self.__get_lighting_configuration(),
            'binnning': self.combo_binning.currentText(),
            'lens': self.combo_lens.currentText(),
            'magnification': self.combo_magnification.currentText(),
            'exposure_time': self.spin_exposure_time.value(),
            'correction': self.line_correction.text(),
            'time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'normalisation': f'type: {self.combo_normalisation_selector.currentText()} ' +
                             f'lower: {self.spin_percentile_lower.value()} ' +
                             f'upper: {self.spin_percentile_upper.value()} ' +
                             f'clip: {self.spin_clip.value()}',
            'contents': 'frame_0',
        }
        if self.button_toggle_averaging.isChecked():
            if self.button_display_subtraction.isChecked():
                meta_data['type'] = 'averaged and subtracted'
                meta_data['averages'] = self.spin_foreground_averages.value()
            else:
                meta_data['type'] = 'averaged'
                meta_data['averages'] = self.spin_foreground_averages.value()
        elif self.button_display_subtraction.isChecked():
            meta_data['type'] = 'subtracted'
            meta_data['averages'] = 1
        else:
            meta_data['type'] = 'single'
            meta_data['averages'] = 1
        match self.__get_magnet_mode():
            case 0:
                meta_data['magnet_mode'] = None
            case 1:  # DC
                meta_data['magnet_mode'] = 'DC'
                meta_data['mag_field'] = self.mag_y[-1]
                meta_data['coil_calib'] = self.combo_calib_file.currentText()
            case 2:  # AC
                meta_data['magnet_mode'] = 'AC'
                meta_data['mag_field'] = self.mag_y[-1]
                meta_data['mag_field_amp'] = self.spin_mag_amplitude.value()
                meta_data['mag_field_freq'] = self.spin_mag_freq.value()
                meta_data['mag_field_offset'] = self.spin_mag_offset.value()
                meta_data['coil_calib'] = self.combo_calib_file.currentText()

        file_path = Path(
            self.line_directory.text()).joinpath(
            datetime.now().strftime("%Y-%m-%d--%H-%M-%S") +
            '_' +
            self.line_prefix.text().strip().replace(' ', '_') +
            '.tiff')
        # file_path.mkdir(parents=True, exist_ok=True)

        tifffile.imwrite(
            str(file_path),
            self.frame_processor.latest_processed_frame.astype(np.uint16),
            photometric='minisblack',
            metadata=meta_data)
        logging.info("Saved file as " + str(file_path))

    def __on_browse(self, event):
        starting_dir = str(Path(r'C:\Users\User\Desktop\USERS'))
        dest_dir = QtWidgets.QFileDialog.getExistingDirectory(
            None,
            'Choose Save Directory',
            starting_dir,
            QtWidgets.QFileDialog.ShowDirsOnly)
        self.line_directory.setText(str(Path(dest_dir)))

    def closeEvent(self, event):
        self.close_event = event
        event.ignore()
        self.image_timer.stop()
        self.plot_timer.stop()
        self.magnetic_field_timer.stop()
        # time.sleep(0.1)
        self.lamp_controller.close(reset=False)
        self.magnet_controller.close(reset=False)
        self.analyser_controller.close(reset=True)
        self.mutex.lock()
        self.camera_grabber.closing = True
        self.camera_grabber.running = False
        self.frame_processor.closing = True
        self.frame_processor.running = False
        self.mutex.unlock()
        cv2.destroyAllWindows()

    def __on_quit_ready(self):
        time.sleep(0.1)
        logging.info("Closing threads and exiting")
        self.camera_thread.quit()
        self.frame_processor_thread.quit()
        super(ArtieLabUI, self).closeEvent(self.close_event)
        sys.exit()


if __name__ == '__main__':
    # Back up the reference to the exceptionhook
    sys._excepthook = sys.excepthook


    def my_exception_hook(exctype, value, traceback):
        # Print the error and traceback
        print("__main__:", exctype, value, traceback)
        # Call the normal Exception hook after
        sys._excepthook(exctype, value, traceback)
        sys.exit(1)


    # Set the exception hook to our wrapping function
    sys.excepthook = my_exception_hook

    app = QtWidgets.QApplication(sys.argv)
    app.setStyle('plastique')
    window = ArtieLabUI()
    try:
        sys.exit(app.exec_())
    except:
        print("__main__: Exiting")
    print(app.exit())
