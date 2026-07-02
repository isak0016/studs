// Node for Max bridge: the `node.script` object in the M4L device loads this.
// Shells out to the studs CLI so push/pull/new-project can run with one
// click from inside Ableton, without leaving the Live Set.
//
// Which project this is comes from a path chosen once via a native folder
// picker ("import" button) and remembered by a `pattr` object in the
// patcher (Max persists pattr values as part of the device's own saved
// state — no file copying, no manual re-pointing after the first choice).
const { execFile } = require("child_process");

const STUDS_REPO = "/Users/isakhaapaniemi/studs/studs";
const PYTHON = `${STUDS_REPO}/venv/bin/python3`; // absolute path — Max's own env PATH is not reliable

function runCli(args, callback) {
  execFile(PYTHON, ["-m", "studs", ...args], { cwd: STUDS_REPO }, (err, stdout, stderr) => {
    const line = (stdout || stderr || String(err)).trim().split("\n").pop();
    callback(line);
  });
}

function confirmCollectAndSave(onConfirmed, onCancelled) {
  const prompt = 'display dialog "Have you run Collect All and Save on this project? '
    + 'Anything not collected will be missing when you share it." '
    + 'buttons {"Cancel", "Yes, continue"} default button "Yes, continue"';
  execFile("osascript", ["-e", prompt], (err) => {
    if (err) {
      onCancelled();
      return;
    }
    onConfirmed();
  });
}

function chooseProjectFolder(callback) {
  const prompt = 'POSIX path of (choose folder with prompt "Choose your Ableton project folder")';
  execFile("osascript", ["-e", prompt], (err, stdout) => {
    if (err) {
      callback(null);
      return;
    }
    callback(stdout.trim());
  });
}

module.exports = { runCli, confirmCollectAndSave, chooseProjectFolder, PYTHON, STUDS_REPO };

// Only wire up the Max-specific side when running inside Node for Max —
// `require("max-api")` throws outside it, which keeps the functions above
// testable standalone with plain `node`.
let Max;
try {
  Max = require("max-api");
} catch {
  Max = null;
}

if (Max) {
  // liveSetPath comes from the patcher's pattr-stored value (see the .maxpat) —
  // not from Live's API, which doesn't expose the project's filesystem path.
  Max.addHandler("sync", (direction, liveSetPath) => {
    runCli([direction, "--live-set", liveSetPath], (line) => Max.outlet(line));
  });

  Max.addHandler("newproject", (liveSetPath) => {
    confirmCollectAndSave(
      () => runCli(["new-project", "--live-set", liveSetPath], (line) => Max.outlet(line)),
      () => Max.outlet("cancelled: run collect all and save first"),
    );
  });

  Max.addHandler("import", () => {
    chooseProjectFolder((folder) => {
      if (!folder) {
        Max.outlet("cancelled: no folder chosen");
        return;
      }
      // Two separate atoms, not one concatenated string — keeps the path
      // (which may contain spaces) intact as its own atom through
      // [route projectfolder] rather than risking it being word-split.
      Max.outlet("projectfolder", folder);
    });
  });
}
