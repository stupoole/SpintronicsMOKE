import numpy as np
from PyQt5 import QtCore
from skimage import exposure
import logging


class FrameProcessor(QtCore.QObject):
    logging.info("FrameProcessor: Initializing FrameProcessor...")
    IMAGE_PROCESSING_NONE = 0
    IMAGE_PROCESSING_PERCENTILE = 1
    IMAGE_PROCESSING_HISTEQ = 2
    IMAGE_PROCESSING_ADAPTEQ = 3
    frame_processed_signal = QtCore.pyqtSignal(np.ndarray, np.float64, tuple)
    diff_processed_signal = QtCore.pyqtSignal(np.ndarray, np.ndarray, np.float64, np.float64, tuple)
    mode = 0
    p_low = 0
    p_high = 100
    clip = 0.03
    subtracting = True
    background = None
    running = False
    frame_counter = 0
    latest_raw_frame = None
    latest_diff_frame_a = None
    latest_diff_frame_b = None
    latest_processed_frame = np.zeros((1024, 1024), dtype=np.uint16)
    latest_hist_data = []
    latest_hist_bins = []
    intensities_y = []
    background = None
    averaging = False
    averages = 16
    running = False

    def __init__(self, parent):
        super().__init__()
        self.parent = parent

    def update_settings(self, settings):
        '''
        :param dict settings: {mode, percentile_lower, percentile_upper, clip_limit}
        :return: None
        '''
        self.mode, self.p_low, self.p_high, self.clip = settings

    def set_mode(self, new_mode):
        if new_mode in [0, 1, 2, 3]:
            self.mode = new_mode
        else:
            logging.info("FrameProcessor: Invalid mode")

    def set_percentile_lower(self, new_percentile):
        if new_percentile < self.p_high:
            self.p_low = new_percentile
        else:
            logging.info("FrameProcessor: Please raise % max to avoid overlap")

    def set_percentile_upper(self, new_percentile):
        if new_percentile > self.p_low:
            self.p_high = new_percentile
        else:
            logging.info("FrameProcessor: Please reduce lower % min to avoid overlap")

    def set_clip_limit(self, new_clip_limit):
        self.clip = new_clip_limit

    def __process_frame(self, frame_in):
        if self.subtracting and self.background is not None:
            frame_in = frame_in - self.background
            frame_in[frame_in < 0] = 0
        match self.mode:
            case self.IMAGE_PROCESSING_NONE:
                return frame_in
            case self.IMAGE_PROCESSING_PERCENTILE:
                px_low, px_high = np.percentile(frame_in, (self.p_low, self.p_high))
                return exposure.rescale_intensity(frame_in, in_range=(px_low, px_high))
            case self.IMAGE_PROCESSING_HISTEQ:
                return (exposure.equalize_hist(frame_in))
            case self.IMAGE_PROCESSING_ADAPTEQ:
                return (exposure.equalize_adapthist(frame_in / 65535, clip_limit=self.clip))
            case _:
                logging.info("FrameProcessor: Unrecognized image processing mode")
                return frame_in

    @QtCore.pyqtSlot()
    def start_processing(self):
        self.running = True
        while self.running:
            got = self.parent.item_semaphore.tryAcquire(1, 1)
            if got:
                logging.debug("Processing Frame")
                item = self.parent.frame_buffer.popleft()
                self.parent.spaces_semaphore.release()
                if type(item) is tuple:
                    logging.debug("Got difference frames")
                    # Diff mode
                    self.latest_diff_frame_a, self.latest_diff_frame_b = item
                    self.intensities_y.append(np.mean(self.latest_diff_frame_a, axis=(0, 1)))
                    self.intensities_y.append(np.mean(self.latest_diff_frame_b, axis=(0, 1)))
                    if self.averaging:
                        if self.frame_counter % self.averages < len(self.diff_frame_stack_a):
                            self.diff_frame_stack_a[self.frame_counter % self.averages] = self.latest_diff_frame_a
                            self.diff_frame_stack_b[self.frame_counter % self.averages] = self.latest_diff_frame_b
                        else:
                            self.diff_frame_stack_a = np.append(self.diff_frame_stack_a,
                                                                np.expand_dims(self.latest_diff_frame_a, 0),
                                                                axis=0)
                            self.diff_frame_stack_b = np.append(self.diff_frame_stack_b,
                                                                np.expand_dims(self.latest_diff_frame_b, 0),
                                                                axis=0)
                        if len(self.diff_frame_stack_a) > self.averages:
                            self.diff_frame_stack_a = self.diff_frame_stack_a[-self.averages:]
                            self.diff_frame_stack_b = self.diff_frame_stack_b[-self.averages:]
                        self.frame_counter += 1
                        mean_a = np.mean(self.diff_frame_stack_a, axis=0)
                        mean_b = np.mean(self.diff_frame_stack_b, axis=0)
                        meaned_diff = np.abs(mean_a.astype(np.int32) - mean_b.astype(np.int32)).astype(np.uint16)
                        self.latest_processed_frame = self.__process_frame(meaned_diff)
                    else:
                        diff_frame = np.abs(self.latest_diff_frame_a.astype(np.int32) - self.latest_diff_frame_b.astype(
                            np.int32)).astype(np.uint16)
                        self.latest_processed_frame = self.__process_frame(diff_frame)
                else:
                    logging.debug("Got single frame")
                    # Single frame mode
                    self.latest_raw_frame = item
                    self.intensities_y.append(np.mean(self.latest_raw_frame, axis=(0, 1)))
                    if self.averaging:
                        if self.frame_counter % self.averages < len(self.raw_frame_stack):
                            self.raw_frame_stack[self.frame_counter % self.averages] = self.latest_raw_frame
                        else:
                            self.raw_frame_stack = np.append(self.raw_frame_stack,
                                                             np.expand_dims(self.latest_raw_frame, 0), axis=0)
                        if len(self.raw_frame_stack) > self.averages:
                            self.raw_frame_stack = self.raw_frame_stack[-self.averages:]
                        self.frame_counter += 1
                        mean_frame = np.mean(self.raw_frame_stack, axis=0)
                        self.latest_processed_frame = self.__process_frame(mean_frame)
                    else:
                        self.latest_processed_frame = self.__process_frame(self.latest_raw_frame)
                self.latest_hist_data, self.latest_hist_bins = exposure.histogram(self.latest_processed_frame)
        logging.info("Stopping Frame Processor")
