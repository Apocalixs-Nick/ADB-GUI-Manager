# ADB GUI Manager

ADB GUI Manager. A script with a user interface that simplifies computer operations without having to type ADB commands into the cmd.

## What it's for

Instead of opening a terminal and remembering the package name and flags for `adb install`, `am force-stop` or `uninstall` every time, you get everything in one window. It detects `adb.exe` and `scrcpy.exe` on the system by itself (or lets you pick them manually), keeps track of connected devices and leaves a log of the operations performed.

The script detects all ADB devices connected to the PC and allows you to select the one on which to perform operations, without disconnecting the other devices.

## Default managed apps

The script allows you to add default apps to the code if you use them frequently. Unlike apps added via the GUI, which are saved in a JSON file, default apps will remain in place even if they are deleted via the GUI. 

For each one, from the interface you can:
- select an APK, a ZIP or a folder (if it's a ZIP or a folder, it looks for the APK inside on its own)
- install it on the selected device (`install -r -g -t`)
- force-stop it
- uninstall it

With the **Add app** / **Delete app** buttons you can add or remove entries from the list.

## Main features

- Automatic detection of `adb` and `scrcpy` (searches the PATH, the typical Windows/Linux folders and the script's own folder); if it can't find them you can point to them manually with "Find ADB" / "Find scrcpy"
- Device selector with online/offline/unauthorized status updated in real time
- Launch scrcpy directly on the selected device
- **Force-stop ALL**: closes all the apps in the list in one shot
- **Reboot**: restart the device
- **Restart ADB**: unlock the connection between the PC and the device (adb kill-server and start-server)
- **Android Settings**: open the Android Settings screen on the selected device
- Operations log always visible, separate from the rest of the interface
- Persistent configuration in `adb_gui_manager.json` (adb/scrcpy path, last used device, app list), saved in the same folder as the script

## Requirements

- `adb.exe` reachable from the PATH or selectable manually
- `scrcpy.exe` if you want to use screen mirroring (optional, without it the rest still works)

## Startup

```
python adb_gui_manager.py
```

On first launch it looks for adb and scrcpy automatically; if it doesn't find them, just use the "Find ADB" / "Find scrcpy" buttons and point to the executable manually. From then on the path stays saved.

## Notes

The `adb_gui_manager.json` file is created/updated automatically next to the script: it contains the adb/scrcpy paths, the last selected device and the current app list. If you want to start fresh, just delete it.

The default apps listed in the script will always be present. **NOTE: Do not push the JSON file.**

```
{
  "apps": [
    {
      "name": "",
      "package": ""
    }
  ],
  "adb_path": "",
  "scrcpy_path": "",
  "last_device": ""
}
```