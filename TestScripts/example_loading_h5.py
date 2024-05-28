import pandas as pd
from tkinter import filedialog
import cv2
from skimage import exposure
import numpy as np

file = filedialog.askopenfilename()
meta_data = pd.read_hdf(file, 'meta_data')
print(meta_data)
contents = meta_data.contents[0]
print(contents)
frames = {}
adapter = cv2.createCLAHE()
adapter.setClipLimit(100)
stream_window = 'window'
cv2.namedWindow(
    stream_window,
    cv2.WINDOW_NORMAL
)
cv2.setWindowProperty(stream_window, cv2.WND_PROP_TOPMOST, 1.0)
cv2.resizeWindow(
    stream_window,
    1024,
    1024)
for i in range(10):
    for item in contents:
        if "frame" in item or "stack" in item:
            data = pd.read_hdf(file, item).values
            if len(data.shape) == 2:
                if data.shape[0] == data.shape[1]:
                    # cv2.imshow(item, data / np.amax(data))
                    cv2.imshow(stream_window, cv2.putText(adapter.apply(data.astype(np.uint16)), item,
                                                          (50, 50),
                                                          0,
                                                          1,
                                                          (255, 255, 255)))
                    cv2.waitKey(20)

cv2.waitKey(0)
cv2.destroyAllWindows()
# myavg = frames["frame_avg"].values
# mybkg = frames["background"].values
# cv2.imshow('averaged', (exposure.equalize_hist(myavg) * 65535).astype(np.uint16))
# cv2.imshow('subtracted', (exposure.equalize_hist(myavg - mybkg) * 65535).astype(np.uint16))
# cv2.waitKey(0)
