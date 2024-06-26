import struct
import numpy as np
import matplotlib.pyplot as plt
from os import listdir
from os.path import isfile, join

start_dir = r'C:\Users\User\PycharmProjects\SpintronicsMOKE\Coil Calibrations\\'
onlyfiles = [f for f in listdir(start_dir) if isfile(join(start_dir, f)) and ".cal" in f]

for file in onlyfiles:

    binaryFile = open(
        start_dir + file,
        mode='rb')

    data = {}
    for i in range(8):
        n_points = struct.unpack('>I', binaryFile.read(4))[0]
        mylist = []
        for point in range(n_points):
            mylist.append(struct.unpack('>d', binaryFile.read(8)))
        data["col_" + str(i)] = mylist

    plt.figure()
    plt.plot(data["col_0"], data["col_1"], 'b+')
    plt.plot(data["col_2"], data["col_3"], 'r-')
    plt.title("Voltage Data")
    plt.xlabel("Voltage (V)")
    plt.ylabel("Field (mT)")
    plt.figure()
    plt.plot(data["col_4"], data["col_5"], 'b+')
    plt.plot(data["col_6"], data["col_7"], 'r-')
    plt.xlabel("Current (A)")
    plt.ylabel("Field (mT)")
    plt.title("Current Data")

    save_data = np.column_stack([list(data.values())[i] for i in [2, 3, 6, 7]])
    header = "Voltage (V), Field (mT), Current (A), Field (mT)"
    savename = start_dir + file.replace(".cal", "_fit.txt")
    np.savetxt(
        savename, save_data, delimiter=', ', header=header)
    print("saving file as: " + savename)

plt.show()
