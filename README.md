# Delta Robot Snack Picking Prototype

An automated snack-picking and weighing system built around a three-axis Delta robot. A Python controller uses an overhead camera, a Qwen vision model, and an ArUco marker to locate products and guide the moving platform. ESP32 firmware drives the servos, vacuum pump relay, and HX711 load cell. A WeChat mini-program displays a cloud-backed cart and order flow.

The system was built and tested as a physical prototype.

## Contents

- [Features](#features)
- [Technology stack](#technology-stack)
- [Architecture](#architecture)
- [Repository structure](#repository-structure)
- [How the pipeline works](#how-the-pipeline-works)
- [Getting started](#getting-started)
- [Configuration and calibration](#configuration-and-calibration)
- [Firmware protocol](#firmware-protocol)
- [Cloud data flow](#cloud-data-flow)
- [Development notes](#development-notes)
- [License](#license)

## Features

| Area | Function |
| --- | --- |
| Product detection | Identify snack names and bounding boxes with qwen3-vl-plus |
| Visual servoing | Track ArUco marker ID 4 and adjust X/Y position with a PID controller |
| Pick and weigh | Control the pump, move items to the weighing station, and read the HX711 |
| Customer interface | Bind a cart by QR code, display weighed items, and create order records |
| Inventory management | Update product names over TCP port 8888 without restarting the controller |

## Technology stack

| Layer | Technology |
| --- | --- |
| Robot controller | Python 3.10+; OpenCV with ArUco support; NumPy; Pillow; DashScope SDK; python-dotenv |
| Vision | USB camera, OpenCV undistortion and ArUco detection, Alibaba Cloud qwen3-vl-plus for product detection |
| Control and communication | Two-axis PID in Python; newline-delimited TCP commands to the ESP32 on port 8266 |
| Firmware | ESP32 Arduino C++, FreeRTOS tasks, Wi-Fi, LEDC servo PWM, HX711 Arduino library, NVS Preferences |
| Customer application | WeChat Mini Program using TypeScript, WXML, and WXSS |
| Cloud backend | WeChat Cloud Development functions in JavaScript/Node.js with wx-server-sdk and a cloud database |
| Inventory desktop tool | Python, Tkinter, ttkbootstrap, pandas, and TCP sockets |
| Design artifacts | EasyEDA schematic/PCB JSON exports, SolidWorks part files, and XLSX bills of materials |


## Architecture

~~~text
Overhead USB camera
       |
       v
Python controller -- image --> qwen3-vl-plus
       |                         |
       |                   names + bounding boxes
       |                         |
       +<------------------------+
       |
       +-- ArUco tracking + PID --> TCP :8266 --> ESP32
       |                                      |--> 3 servo outputs
       |                                      |--> pump relay and button
       |                                      +--> HX711 load cell
       |
       +-- weighted item --> machineWeigh HTTP trigger
                                      |
                                      v
                              WeChat cloud database
                                      ^
                                      |
                        WeChat Mini Program polls cart
~~~

The camera provides image-space feedback. The firmware's **[OK]** response means a move was accepted and queued; **pos** reports the most recently commanded coordinates.

## Repository structure

~~~text
Delta-Snack-Picking-Robot/
├── README.md
├── LICENSE
├── BOM/                    Electrical and structural bills of materials
├── circuit/                EasyEDA schematic and PCB exports
├── models/                 SolidWorks part files
├── video.mp4               Demonstration media
└── src/
    ├── README.md           Detailed setup guide
    ├── .env.example         DashScope key template
    ├── controller/          Camera, vision, PID, TCP, pick, and cloud client
    ├── firmware/            ESP32 Arduino sketch
    ├── configer/            Desktop inventory-distribution tool
    └── wxapp/               Mini-program and cloud functions
~~~

The main implementation files are [main.py](src/controller/main.py), [vision.py](src/controller/vision.py), [servo.py](src/controller/servo.py), [robot.py](src/controller/robot.py), [cloud.py](src/controller/cloud.py), and [delta_robot.ino](src/firmware/delta_robot.ino). See the [setup guide](src/README.md) for module-level details.

## How the pipeline works

1. **Capture and detect.** The controller requests 1920 × 1080 camera frames. If dot25.npz is loaded, OpenCV corrects lens distortion. Press **s** to start the scan loop. A frame is sent to the configured vision model, which returns item names and normalized bounding boxes.
2. **Locate the end effector.** The controller detects an ArUco DICT_4X4_50 marker with ID 4 on the moving platform. A scan is skipped when the marker is not visible.
3. **Align.** For each detected item, the controller compares its box center with the marker center. The PID output is mapped to robot X/Y movement, clamped to a maximum step and workspace, and sent over TCP. The loop stops below a 15-pixel error by default or after 25 iterations.
4. **Pick.** The platform descends in 5 mm increments. The firmware toggles the pump and sends an event when its GPIO 27 button is pressed. The controller then lifts the platform and requests the weighing position.
5. **Weigh.** The firmware samples the HX711 before and after pump release, separated by a one-second delay, and returns the difference in grams.
6. **Report and rescan.** A positive weight is posted to the cloud function when its URL is configured. The controller scans again immediately after processing a detected batch; if no target is found, it retries after ten seconds.

One model scan supplies target positions for a batch. The next scan starts after the batch has been processed.

## Getting started

The following steps cover firmware, controller, and optional cloud configuration.

### 1. Prepare firmware and hardware

Use an ESP32-compatible Arduino environment with the HX711 library and open [delta_robot.ino](src/firmware/delta_robot.ino). Connect the three servos to GPIO **14, 12, 13**, the pump relay to **26**, the button to **27**, and HX711 DOUT/SCK to **33/32**.

The firmware stores Wi-Fi credentials in NVS. The command **setwifi SSID password** writes them; reboot to apply them. Serial and TCP both feed the command handler. Check the electrical design, motion limits, and pump polarity before powering the robot.

### 2. Prepare the Python controller

Install the Python dependencies:

~~~shell
python -m pip install opencv-contrib-python numpy pillow dashscope python-dotenv
~~~

From **src/**, copy **.env.example** to **.env** and set **DASHSCOPE_API_KEY**. The configuration module requires this variable even with **--no-robot**.

Set **ROBOT_IP** and calibration values in [config.py](src/controller/config.py). Update the camera intrinsics and inventory files for the installation.

### 3. Run

~~~shell
cd src
python -m controller.main
~~~

| Option | Purpose |
| --- | --- |
| **--camera N** | Select the camera index; default 0 |
| **--intrinsics PATH** | Select the calibration NPZ file |
| **--ip ADDRESS**, **--port N** | Override the robot TCP endpoint |
| **--no-robot** | Skip the robot connection and physical moves |
| **--save** | Save annotated detection images |

Press **s** to start the scan loop and **q** to quit. **--no-robot** skips robot communication and physical movement while retaining the vision and servo-loop code path.

### 4. Configure the optional WeChat demonstration

1. Replace **YOUR_ENV_ID** in [app.ts](src/wxapp/miniprogram/app.ts) with a cloud environment ID.
2. Deploy the four functions under [cloudfunctions](src/wxapp/cloudfunctions/) and create the **products**, **carts**, and **orders** collections. The optional **config** collection supplies the displayed logo.
3. Create product records whose **name** exactly matches expected vision names. Each product uses **unitPrice** as the price per 500 g and may include an **image**.
4. Enable the machineWeigh HTTP trigger and set **MACHINE_WEIGH_URL** in [config.py](src/controller/config.py). The default value is empty, so cloud posting is disabled until configured.
5. Set **CART_ID** to match the machine's QR code content. The mini-program validates its format and polls the cart record every three seconds.

## Configuration and calibration

| Setting | Default | Meaning |
| --- | ---: | --- |
| **ROBOT_PORT** | 8266 | ESP32 TCP command port |
| **INVENTORY_SERVER_PORT** | 8888 | TCP listener for inventory updates |
| **SERVO_KP / KI / KD** | 0.30 / 0 / 0.10 | Pixel-error PID gains |
| **SERVO_TOL_PX** | 15 | Alignment threshold |
| **SERVO_MAX_ITER** | 25 | Maximum alignment iterations |
| **SERVO_MAX_STEP_MM** | 50 | Maximum single PID move |
| **CAMERA_OFFSET_U / V** | −70 / −40 px | Camera alignment offsets |
| **DESCEND_STEP_MM** | 5 | Per-command descent increment |
| **WEIGH_X / Y / Z_MM** | 195 / 0 / 150 | Weighing position |

These values are in [config.py](src/controller/config.py). The firmware independently limits ordinary coordinates to X/Y **−100…100 mm** and Z **50…280 mm**, with the weighing position exempted. Keep host and firmware limits consistent. The HX711 factor defaults to **2280.0**; calibrate it with a known mass.

The optional [dot25.npz](src/controller/dot25.npz) stores **mtx** and **dist** camera-calibration arrays. If it cannot be read, undistortion is skipped. The controller requests a resolution; the actual image size depends on the camera and driver. Recalibrate the image-to-robot mapping and offsets after moving the camera.

## Firmware protocol

The firmware accepts UTF-8, newline-delimited text on TCP port 8266 and sends a **[HELLO]** line when connected. Its main commands are:

| Command | Response or effect |
| --- | --- |
| **x,y,z** | Request a Cartesian move; **[OK]** means queued, **[ERR]** means rejected |
| **pos** | Return last commanded coordinates and servo angles |
| **home** | Request the firmware home coordinates |
| **pump_on / pump_off / pump** | Set or query the pump relay |
| **weight** | Queue a differential load-cell reading; later return **[WEIGHT]** |
| **tare** | Zero the load cell |
| **ping** | Return **[PONG]** |
| **setwifi SSID password** | Save Wi-Fi credentials to NVS through the shared serial/TCP handler |

The firmware can also push a **[PUMP] ON (button)** message when the physical button toggles the relay. The controller uses this event during descent. A TCP client idle for roughly 30 seconds is disconnected; the Python controller sends periodic pings.

## Cloud data flow

~~~text
Python cloud.py -- one item --> machineWeigh
                                  |-- lookup products.name
                                  |-- compute weight × unitPrice / 500
                                  +-- write carts.snackList
                                             |
WeChat mini-program <-- getCartList ---------+
        |
        +-- simulated payment --> completeOrder --> orders + cart deletion
~~~

The relevant cloud functions are [machineWeigh](src/wxapp/cloudfunctions/machineWeigh/index.js), [getCartList](src/wxapp/cloudfunctions/getCartList/index.js), [validateCart](src/wxapp/cloudfunctions/validateCart/index.js), and [completeOrder](src/wxapp/cloudfunctions/completeOrder/index.js).

The Python client posts one weighed item per request. machineWeigh writes that request's snackList to the cart, so the current flow displays the latest reported item. validateCart checks the QR code's ID format and allows binding before the first item is reported. The mini-program simulates checkout and then calls completeOrder to create the order record.

## Development notes

- The inventory list guides the vision-model prompt. Add a name check after inference when strict catalog matching is required.
- machineWeigh replaces snackList on each report. Merge with the existing cart if the application needs multiple picks in one checkout.
- The payment handler uses a simulated success flow. Integrate a payment service before using it for transactions.
- Serial and TCP commands share the firmware handler, including **setwifi**. Restrict network access to the robot and add command authorization for networked deployments.
- Calibrate camera mapping, robot geometry, HX711 scaling, and motion timing for each installation.

## License

See [LICENSE](LICENSE) for the included GPL v3 license text.
