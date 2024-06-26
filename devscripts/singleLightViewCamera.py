from pylablib.devices import DCAM
import time
import cv2
from WrapperClasses.LampController import LampController
import skimage

cam = DCAM.DCAMCamera(idx=0)
cam.set_attribute_value("EXPOSURE TIME", 0.05)
height, width = cam.get_detector_size()
cam.set_roi(hbin=2, vbin=2)

fullscreen = True

stream_window = 'HamamatsuView'
if fullscreen:
    window_width = width // 2
    window_height = height // 2
    cv2.namedWindow(
        stream_window,
        flags=(cv2.WINDOW_NORMAL | cv2.WINDOW_GUI_NORMAL | cv2.WINDOW_FREERATIO))
    cv2.setWindowProperty(stream_window, cv2.WND_PROP_TOPMOST, 1.0)
    cv2.setWindowProperty(stream_window, cv2.WND_PROP_FULLSCREEN, 1.0)
    cv2.resizeWindow(
        stream_window,
        window_width,
        window_height)
    cv2.moveWindow(
        stream_window,
        0,
        0)

lampController = LampController()
lampController.enable_left_pair()

cam.setup_acquisition()
cam.start_acquisition()

running = True  # press esc to close
i=0
while running:
    frame = cam.read_newest_image()
    if frame is not None:
        cv2.imshow(stream_window, skimage.exposure.equalize_hist(frame))
        k = cv2.waitKey(1)
        if i == 0:
            ROI = cv2.selectROI(stream_window, skimage.exposure.equalize_hist(frame))
            print(ROI)
            i = 1

    k = cv2.waitKey(1)
    if k == 27:
        running = False

cam.close()
lampController.disable_all()
time.sleep(0.2)
lampController.close()
cv2.destroyAllWindows()
