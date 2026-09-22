# Delta Robot Snack Picking Prototype: Source Guide

This directory contains the Python controller, ESP32 firmware, WeChat mini-program, and cloud functions for the physical prototype. See the [project overview](../README.md).

## Technology stack

| Component | Technology |
| --- | --- |
| Controller and vision | Python 3.10+, OpenCV/ArUco, NumPy, Pillow, DashScope SDK, python-dotenv |
| Robot firmware | ESP32 Arduino C++, FreeRTOS, Wi-Fi/TCP, LEDC PWM, HX711 library, NVS Preferences |
| Mini-program | TypeScript, WXML, WXSS, WeChat Mini Program APIs |
| Cloud functions | JavaScript/Node.js, wx-server-sdk, WeChat Cloud Development database |
| Inventory tool | Python, Tkinter, ttkbootstrap, pandas, TCP sockets |

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
2. Pressing **s** calls the vision model for snack names and bounding boxes. If inventory.json is available, its names are added to the prompt.
3. The controller uses the pixel error between the marker and an item's center to calculate X/Y moves with a PID controller. It sends the moves over TCP until the error reaches a threshold or the iteration limit is exceeded.
4. The platform descends in steps. The GPIO 27 contact button toggles the pump and sends an event to Python.
5. At the weighing position, the firmware samples the HX711 twice. Python switches off the pump between the readings, and the firmware returns their difference.
6. If the weight is positive and a cloud-function URL is configured, Python posts the product name and weight. The mini-program polls the cart record every three seconds.

A firmware **[OK]** response means a move was accepted and queued, not that the platform arrived. The **pos** command reports the last commanded coordinates, not a separately measured physical position.

## Setup

Configure the firmware, controller, and cloud components in that order.

### ESP32

- Open [firmware/delta_robot.ino](firmware/delta_robot.ino) in an ESP32 Arduino environment with the HX711 library.
- Connect the three servos to GPIO 14/12/13, the pump relay to GPIO 26, the button to GPIO 27, and HX711 DOUT/SCK to GPIO 33/32.
- The firmware reads Wi-Fi credentials from NVS. The **setwifi SSID password** command saves them; reboot to apply them. Serial and TCP commands use the same handler.
- Calibrate the HX711 factor, robot geometry, weighing position, and motion limits for the installation.

### Python controller

- Use Python 3.10+ with OpenCV/ArUco, NumPy, Pillow, DashScope, and python-dotenv.
- Copy **.env.example** to **.env** and set **DASHSCOPE_API_KEY**. The configuration module reads this variable even with **--no-robot**.
- Set the robot IP, camera offsets, PID values, and positions in [controller/config.py](controller/config.py). The dot25.npz camera calibration must match the camera in use; the program skips undistortion if it cannot load the file.
- Edit [controller/inventory.json](controller/inventory.json), or push an inventory list from configer to the controller's TCP port 8888. A push updates the running list and writes the file.
- From this directory, run **python -m controller.main**. Available options include **--camera**, **--intrinsics**, **--ip**, **--port**, **--no-robot**, and **--save**. Press **s** to start automatic scanning and **q** to exit.

**--no-robot** skips the TCP connection and physical movements, but the program still attempts visual servoing after a detection.

### WeChat cloud demonstration

1. Replace **YOUR_ENV_ID** in [wxapp/miniprogram/app.ts](wxapp/miniprogram/app.ts) with the cloud environment ID.
2. Deploy the four cloud functions and create the products, carts, and orders collections. The optional config collection supplies the displayed brand image.
3. Product names in products must match the names returned by vision for pricing. machineWeigh calculates a subtotal from weight and price per 500 g.
4. Enable the machineWeigh HTTP trigger and set **MACHINE_WEIGH_URL** in [controller/config.py](controller/config.py). Its default is empty, so posting begins after configuration.
5. The mini-program scans a QR code containing a cart ID and polls the cloud record. validateCart mainly checks ID syntax; it can allow binding even before a matching carts record exists.

## Integration notes

- [machineWeigh](wxapp/cloudfunctions/machineWeigh/index.js) writes the latest reported item to snackList. Merge records there if a cart should hold multiple picks.
- The [mini-program payment handler](wxapp/miniprogram/pages/index/index.ts) simulates checkout and calls completeOrder. Connect a payment service for transactions.
- The inventory list guides the model prompt. Add a post-inference name check for strict catalog matching.
- Serial and TCP share the firmware command handler. Add authorization to the robot and the cloud weighing endpoint for networked deployments.

Recalibrate the camera, suction mechanism, load cell, and motion boundaries after mechanical changes.
