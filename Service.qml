import QtQuick
import Quickshell
import Quickshell.Io

// Supervises the Python daemon that owns the Touch Bar. The daemon is a plain
// child process: it renders SVG and rewrites tiny-dfr's config, so all this
// item has to do is keep it alive and surface its logs.
Item {
  id: root

  // Qt.resolvedUrl(".") is the plugin directory while this file is loaded from
  // ~/.config/omarchy/plugins/ewweberlin.touchbar/.
  readonly property string pluginDir: decodeURIComponent(
    String(Qt.resolvedUrl(".")).replace(/^file:\/\//, "").replace(/\/+$/, ""))
  readonly property string entryPoint: root.pluginDir + "/daemon/__main__.py"
  readonly property string python: "python3"

  property int failures: 0
  property bool shuttingDown: false

  function start() {
    if (shuttingDown || proc.running) return
    // -B keeps Python from writing __pycache__ into the plugin folder: the
    // shell watches that folder and would reload the plugin, killing the
    // daemon, the first time it imported its own modules. daemon/__main__.py
    // sets sys.dont_write_bytecode as well, because the flag does not survive
    // every launcher.
    proc.command = [root.python, "-B", root.entryPoint, "--managed"]
    proc.running = true
  }

  Component.onCompleted: {
    console.log("[touchbar] plugin dir:", root.pluginDir)
    root.start()
  }

  // The daemon watches its parent pid too, but stopping it here keeps the
  // Touch Bar from showing a stale frame after the plugin is disabled.
  Component.onDestruction: {
    root.shuttingDown = true
    proc.running = false
  }

  Process {
    id: proc
    onExited: (exitCode, exitStatus) => {
      if (root.shuttingDown) return
      console.warn("[touchbar] daemon exited with", exitCode, "status", exitStatus)
      root.failures = Math.min(root.failures + 1, 6)
      // 0.5s, 1s, 2s, 4s, 8s, 16s, then every 30s.
      retry.interval = root.failures >= 6 ? 30000 : 500 * Math.pow(2, root.failures - 1)
      retry.restart()
    }
    stdout: SplitParser {
      onRead: line => console.log("[touchbar]", line)
    }
    stderr: SplitParser {
      onRead: line => console.warn("[touchbar]", line)
    }
  }

  Timer {
    id: retry
    repeat: false
    onTriggered: root.start()
  }

  // A long healthy run resets the backoff so a crash much later still retries
  // quickly instead of waiting out the maximum interval.
  Timer {
    interval: 60000
    running: proc.running
    repeat: true
    onTriggered: if (proc.running) root.failures = 0
  }
}
