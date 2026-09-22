# Delta Robot Snack Picking Prototype

An experimental snack-picking and weighing system built around a three-axis Delta robot. A Python controller uses an overhead camera, a Qwen vision model, and an ArUco marker to locate products and visually guide the moving platform. ESP32 firmware drives the servos, vacuum pump relay, and HX711 load cell. A WeChat mini-program demonstrates a cloud-backed cart.

> **Archive status:** The original device and parts are no longer available. This README documents the checked-in implementation, not a newly tested deployment. The repository does not contain repeatable measurements of recognition accuracy, pick success, weighing error, or continuous operation.

## Contents

- [Features and current scope](#features-and-current-scope)
- [Technology stack](#technology-stack)
- [Architecture](#architecture)
- [Repository structure](#repository-structure)
- [How the pipeline works](#how-the-pipeline-works)
- [Getting started](#getting-started)
- [Configuration and calibration](#configuration-and-calibration)
- [Firmware protocol](#firmware-protocol)
- [Cloud data flow](#cloud-data-flow)
- [Known limitations](#known-limitations)
- [License](#license)

## Features and current scope

| Area | Implemented in this repository | Qualification |
| --- | --- | --- |
| Visual detection | Sends a camera image to qwen3-vl-plus and parses product names and bounding boxes | Results depend on the external model; the optional inventory list is a prompt, not an enforced allowlist |
| Visual servoing | Tracks ArUco marker ID 4 and sends PID-adjusted X/Y moves | Requires the original camera and robot geometry to be recalibrated on another build |
| Picking | Moves down in steps and responds to a pump-button event | The code detects a button press; physical contact depends on the mechanical arrangement |
| Weighing | Uses an HX711 to report a before/after load-cell difference | No measured calibration accuracy is included |
| Customer interface | QR cart binding, three-second polling, product display, and order-record creation | The payment screen is a simulation; there is no payment-service call |
| Inventory tool | Receives product-name updates over TCP port 8888 | Names are written to inventory.json while the controller is running |

## Technology stack

| Layer | Technology used in the source |
| --- | --- |
| Robot controller | Python 3.10+; OpenCV with ArUco support; NumPy; Pillow; DashScope SDK; python-dotenv |
| Vision | USB camera, OpenCV undistortion and ArUco detection, Alibaba Cloud qwen3-vl-plus for product detection |
| Control and communication | Two-axis PID in Python; newline-delimited TCP commands to the ESP32 on port 8266 |
| Firmware | ESP32 Arduino C++, FreeRTOS tasks, Wi-Fi, LEDC servo PWM, HX711 Arduino library, NVS Preferences |
| Customer application | WeChat Mini Program using TypeScript, WXML, and WXSS |
| Cloud backend | WeChat Cloud Development functions in JavaScript/Node.js with wx-server-sdk and a cloud database |
| Inventory desktop tool | Python, Tkinter, ttkbootstrap, pandas, and TCP sockets |
| Design artifacts | EasyEDA schematic/PCB JSON exports, SolidWorks part files, and XLSX bills of materials |

The repository has no pinned Python dependency manifest or verified cross-platform installation matrix. Package names above come from imports and project files, not from a fresh dependency-resolution test.

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

The camera provides image-space feedback. The firmware's **[OK]** response means a move was accepted and queued, and its **pos** response reports the most recently commanded coordinates rather than a separate position measurement.

## Repository structure

~~~text
Delta-Snack-Picking-Robot/
├── README.md
├── LICENSE
├── BOM/                    Electrical and structural bills of materials
├── circuit/                EasyEDA schematic and PCB exports
├── models/                 SolidWorks part files
├── video.mp4               Archived demonstration media
└── src/
    ├── README.md           Source-level setup guide
    ├── .env.example         DashScope key template
    ├── controller/          Camera, vision, PID, TCP, pick, and cloud client
    ├── firmware/            ESP32 Arduino sketch
    ├── configer/            Desktop inventory-distribution tool
    └── wxapp/               Mini-program and cloud functions
~~~

The main implementation files are [main.py](src/controller/main.py), [vision.py](src/controller/vision.py), [servo.py](src/controller/servo.py), [robot.py](src/controller/robot.py), [cloud.py](src/controller/cloud.py), and [delta_robot.ino](src/firmware/delta_robot.ino). More source-level detail is in the [English source guide](src/README.md).

## How the pipeline works

1. **Capture and detect.** The controller requests 1920 × 1080 camera frames. If dot25.npz is loaded, OpenCV corrects lens distortion. Press **s** to start the scan loop. A frame is sent to the configured vision model, which returns item names and normalized bounding boxes.
2. **Locate the end effector.** The controller detects an ArUco DICT_4X4_50 marker with ID 4 on the moving platform. A scan is skipped when the marker is not visible.
3. **Align.** For each detected item, the controller compares its box center with the marker center. The PID output is mapped to robot X/Y movement, clamped to a maximum step and workspace, and sent over TCP. The loop stops below a 15-pixel error by default or after 25 iterations.
4. **Pick.** The platform descends in 5 mm increments. The firmware toggles the pump and sends an event when its GPIO 27 button is pressed. The controller then lifts the platform and requests the weighing position.
5. **Weigh.** The firmware samples the HX711 before and after pump release, separated by a one-second delay, and returns the difference in grams.
6. **Report and rescan.** A positive weight is posted to the cloud function when its URL is configured. The controller scans again immediately after processing a detected batch; if no target is found, it retries after ten seconds.

The initial target positions come from one detection frame. The code does not re-detect every remaining item between picks within the same batch; a new model scan occurs after that batch.

## Getting started

These steps describe how the archived source is configured. The hardware is unavailable, so the sequence has not been rerun in the present environment.

### 1. Prepare firmware and hardware

Use an ESP32-compatible Arduino environment with the HX711 library and open [delta_robot.ino](src/firmware/delta_robot.ino). The source assigns the three servos to GPIO **14, 12, 13**, the pump relay to **26**, the button to **27**, and HX711 DOUT/SCK to **33/32**.

The firmware stores Wi-Fi credentials in NVS. The command **setwifi SSID password** writes them; reboot to apply them. **Current code accepts this command through the shared serial/TCP command handler**, so the previous claim that it is serial-only was incorrect. Recheck the electrical design, motion limits, and pump polarity before powering a new build.

### 2. Prepare the Python controller

The source imports OpenCV with the ArUco module, NumPy, Pillow, DashScope, and python-dotenv. An example installation command is:

~~~shell
python -m pip install opencv-contrib-python numpy pillow dashscope python-dotenv
~~~

The package set is derived from source imports and has not been tested against current releases. From **src/**, copy **.env.example** to **.env** and set **DASHSCOPE_API_KEY**. The configuration module requires this variable even with **--no-robot**.

Set **ROBOT_IP** and any setup-specific calibration values in [config.py](src/controller/config.py). The camera intrinsics file, inventory list, and robot IP are local configuration rather than universal defaults.

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

Press **s** to start the scan loop and **q** to quit. **--no-robot** still enters a visual-servo simulation after detection; it is useful for inspecting parts of the vision path but cannot validate a physical pick.

### 4. Configure the optional WeChat demonstration

1. Replace **YOUR_ENV_ID** in [app.ts](src/wxapp/miniprogram/app.ts) with a cloud environment ID.
2. Deploy the four functions under [cloudfunctions](src/wxapp/cloudfunctions/) and create the **products**, **carts**, and **orders** collections. The optional **config** collection supplies the displayed logo.
3. Create product records whose **name** exactly matches expected vision names. Each product uses **unitPrice** as the price per 500 g and may include an **image**.
4. Enable the machineWeigh HTTP trigger and set **MACHINE_WEIGH_URL** in [config.py](src/controller/config.py). The checked-in value is empty, so cloud posting is disabled by default.
5. Set **CART_ID** to match the machine's QR code content. The mini-program validates its format and polls the cart record every three seconds.

## Configuration and calibration

| Setting | Checked-in value | Meaning |
| --- | ---: | --- |
| **ROBOT_PORT** | 8266 | ESP32 TCP command port |
| **INVENTORY_SERVER_PORT** | 8888 | TCP listener for inventory updates |
| **SERVO_KP / KI / KD** | 0.30 / 0 / 0.10 | Pixel-error PID gains |
| **SERVO_TOL_PX** | 15 | Alignment threshold |
| **SERVO_MAX_ITER** | 25 | Maximum alignment iterations |
| **SERVO_MAX_STEP_MM** | 50 | Maximum single PID move |
| **CAMERA_OFFSET_U / V** | −70 / −40 px | Offsets for the original camera installation |
| **DESCEND_STEP_MM** | 5 | Per-command descent increment |
| **WEIGH_X / Y / Z_MM** | 195 / 0 / 150 | Original weighing position |

These values are in [config.py](src/controller/config.py). The firmware independently limits ordinary coordinates to X/Y **−100…100 mm** and Z **50…280 mm**, with the weighing position exempted. Keep host and firmware limits consistent on a new build. The HX711 factor in the firmware defaults to **2280.0** and requires a known mass for calibration.

The optional [dot25.npz](src/controller/dot25.npz) stores **mtx** and **dist** camera-calibration arrays. If it cannot be read, undistortion is skipped. The controller requests a resolution; the actual image size depends on the camera and driver. The image-to-robot axis mapping and camera offsets must be measured again after moving the camera.

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
| **setwifi SSID password** | Save Wi-Fi credentials to NVS; currently reachable via serial and TCP |

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

The Python client posts **one item per request**, while machineWeigh **replaces** the cart's snackList with the current request's list. Consecutive picks therefore do not reliably accumulate in one cart. validateCart checks ID syntax and allows binding without an existing cart document. The mini-program's payment handler uses a timer to simulate success and does not call a payment service.

## License

See [LICENSE](LICENSE) for the included GPL v3 license text.
