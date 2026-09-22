# Delta Robot Snack Picking Prototype: Source Guide

This directory contains the Python controller, ESP32 firmware, WeChat mini-program, and cloud functions. The original hardware and parts are no longer available. This guide describes the checked-in code for readers or anyone rebuilding the prototype; it does **not** imply that the current version has been revalidated on hardware. See the [project overview](../README.md).

## Source layout

| Path | Purpose |
| --- | --- |
| [controller/main.py](controller/main.py) | Program entry point, camera preview, scanning, and pick loop |
| [controller/vision.py](controller/vision.py) | qwen3-vl-plus calls, detection parsing, and ArUco recognition |
| [controller/servo.py](controller/servo.py) | Pixel-error PID control, stepwise descent, transfer, and weighing |
| [controller/robot.py](controller/robot.py) | TCP text-protocol client for the ESP32 |
| [controller/camera.py](controller/camera.py) | Camera capture and optional lens-distortion correction |
| [controller/cloud.py](controller/cloud.py) | Posts product names and weights to a cloud function |
| [controller/config.py](controller/config.py) | IP address, workspace, PID, positions, and other settings |
| [firmware/delta_robot.ino](firmware/delta_robot.ino) | ESP32 motion, pump, button, weighing, and network tasks |
| [wxapp](wxapp/) | Mini-program and the machineWeigh, getCartList, validateCart, and completeOrder cloud functions |
| [configer](configer/) | Desktop inventory tool that can push product names to the controller over TCP |

## Code path

1. The Python program reads camera frames and looks for ArUco marker ID 4 on the moving platform.
2. Pressing **s** calls the vision model for snack names and bounding boxes. If inventory.json is available, its names are added to the prompt. The returned names are not checked against that list.
3. The controller uses the pixel error between the marker and an item's center to calculate X/Y moves with a PID controller. It sends the moves over TCP until the error reaches a threshold or the iteration limit is exceeded.
4. The platform descends in steps. When the firmware sees a press on the GPIO 27 button, it toggles the pump and sends a message to Python. Whether the button press indicates contact with an item depends on the physical mechanism.
5. At the weighing position, the firmware samples the HX711 twice. Python switches off the pump between the readings, and the firmware returns their difference.
6. If the weight is positive and a cloud-function URL is configured, Python posts the product name and weight. The mini-program polls the cart record every three seconds.

A firmware **[OK]** response means a move was accepted and queued, not that the platform arrived. The **pos** command reports the last commanded coordinates, not a separately measured physical position.

## Configuration for a new build

These are settings required by the source, not installation steps verified on a new device.

### ESP32

- Open [firmware/delta_robot.ino](firmware/delta_robot.ino) in an ESP32 Arduino environment with the HX711 library.
- The source assigns GPIO 14/12/13 to the three servos, GPIO 26 to the pump relay, GPIO 27 to the button, and GPIO 33/32 to the HX711 DOUT/SCK pins.
- The firmware reads Wi-Fi credentials from NVS. The **setwifi SSID password** command saves them; reboot to apply them. **Serial and TCP commands enter the same handler in the current firmware, so setwifi is not restricted to the serial port.**
- Recheck the HX711 calibration factor, robot geometry, weighing position, and motion limits against any new hardware.

### Python controller

- The source uses Python 3.10+ syntax and imports OpenCV with ArUco support, NumPy, Pillow, DashScope, and python-dotenv.
- Copy **.env.example** to **.env** and set **DASHSCOPE_API_KEY**. The configuration module reads this variable even with **--no-robot**.
- Set the robot IP, camera offsets, PID values, and positions in [controller/config.py](controller/config.py). The dot25.npz camera calibration must match the camera in use; the program skips undistortion if it cannot load the file.
- Edit [controller/inventory.json](controller/inventory.json), or push an inventory list from configer to the controller's TCP port 8888. A push updates the running list and writes the file.
- From this directory, run **python -m controller.main**. Available options include **--camera**, **--intrinsics**, **--ip**, **--port**, **--no-robot**, and **--save**. Press **s** to start automatic scanning and **q** to exit.

**--no-robot** skips the TCP connection and physical movements, but the program still attempts visual servoing after a detection. It cannot establish a physical pick success rate.

### WeChat cloud demonstration

1. Set the cloud environment ID in [wxapp/miniprogram/app.ts](wxapp/miniprogram/app.ts); the repository still contains the **YOUR_ENV_ID** placeholder.
2. Deploy the four cloud functions and create the products, carts, and orders collections. The optional config collection supplies the displayed brand image.
3. Product names in products must match the names returned by vision for pricing. machineWeigh calculates a subtotal from weight and price per 500 g.
4. Enable the machineWeigh HTTP trigger and set **MACHINE_WEIGH_URL** in [controller/config.py](controller/config.py). Its repository default is empty, so posting is disabled by default.
5. The mini-program scans a QR code containing a cart ID and polls the cloud record. validateCart mainly checks ID syntax; it can allow binding even before a matching carts record exists.

## Known limitations

- [machineWeigh](wxapp/cloudfunctions/machineWeigh/index.js) **replaces** the stored snackList with the current request's list. Because Python sends one item per request, consecutive picks do not reliably accumulate in one cart.
- The [mini-program payment handler](wxapp/miniprogram/pages/index/index.ts) simulates success and calls completeOrder; it does not invoke a payment service.
- The inventory list only guides the model prompt. There is no post-inference allowlist check.
- The cloud weighing endpoint has no machine authentication implemented in this source. The firmware also allows a TCP command to reach the setwifi handler.
- This repository has no reproducible pick-success, weighing-error, or long-running test records. The original equipment is no longer available for retesting.

This repository records the prototype's implementation state. A new build would require review of motion boundaries and power-loss behavior, followed by camera, suction, load-cell, and cloud-flow calibration.
