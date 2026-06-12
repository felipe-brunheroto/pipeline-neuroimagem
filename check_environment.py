"""
check_environment.py
====================

PURPOSE
-------
Before we build the DICOM -> NIfTI -> BIDS-like pipeline, we need to be sure
the tools it depends on are actually installed and working. This script does
exactly that: it runs a few harmless checks and prints a clear, human-readable
report telling you what is ready and what is still missing.

It changes NOTHING on your computer. It only looks and reports.

WHAT IT CHECKS
--------------
1. Python version            -> the pipeline needs Python 3.8 or newer.
2. dcm2niix                  -> the external tool that does the actual
                                DICOM -> NIfTI conversion. This is REQUIRED.
3. pydicom (Python library)  -> lets Python read DICOM tags (needed later for
                                provenance and validation). RECOMMENDED.
4. nibabel (Python library)  -> optional helper for reading NIfTI files.
                                OPTIONAL - only if we ever need it.

HOW TO RUN IT
-------------
Open a terminal (on Windows: "Command Prompt" or "PowerShell"), then type:

    python check_environment.py

If "python" is not recognized, try:

    py check_environment.py

Read the report at the bottom. Anything marked [MISSING] needs to be installed
before the pipeline will work. The report tells you how.
"""

# We use ONLY Python standard-library modules here so this script always runs,
# even before anything else is installed.
import sys           # to read the Python version
import shutil        # to find external programs on the system PATH
import subprocess    # to actually run dcm2niix and read its version
import importlib     # to test whether a Python library can be imported


# A tiny helper so every line of the report looks the same and is easy to scan.
def report_line(label, status, detail=""):
    """
    Print one neatly aligned line of the report.

    label  : what we checked (e.g. "Python version")
    status : a short tag, e.g. "[OK]", "[MISSING]", "[WARN]"
    detail : extra info or instructions for the user
    """
    # ljust() pads the label with spaces so the status tags line up in a column.
    print(f"  {label.ljust(22)} {status.ljust(11)} {detail}")


def check_python():
    """Check that Python is version 3.8 or newer."""
    major = sys.version_info.major   # e.g. 3
    minor = sys.version_info.minor   # e.g. 11
    version_text = f"{major}.{minor}.{sys.version_info.micro}"

    # We require Python 3.8+. This is a safe, widely available baseline.
    if major == 3 and minor >= 8:
        report_line("Python version", "[OK]", f"Python {version_text}")
        return True
    else:
        report_line(
            "Python version",
            "[TOO OLD]",
            f"Found {version_text}; please install Python 3.8 or newer.",
        )
        return False


def check_dcm2niix():
    """
    Check that dcm2niix is installed and reachable from the command line.

    'reachable from the command line' means it is on the system PATH, so that
    typing 'dcm2niix' in a terminal works from any folder. The pipeline will
    call it the same way, so this is exactly the right thing to test.
    """
    # shutil.which() returns the full path to the program if it can be found,
    # or None if it cannot. This is the simplest reliable way to detect it.
    location = shutil.which("dcm2niix")

    if location is None:
        report_line(
            "dcm2niix",
            "[MISSING]",
            "REQUIRED. See install instructions printed below.",
        )
        return False

    # If we found it, try to run it with the version flag to confirm it works.
    # We wrap this in try/except so a broken install reports gracefully instead
    # of crashing the whole script.
    try:
        result = subprocess.run(
            ["dcm2niix", "--version"],
            capture_output=True,   # capture the text it prints
            text=True,             # give us strings, not raw bytes
            timeout=30,            # never hang forever
        )
        # dcm2niix prints its version to stdout (and sometimes stderr).
        version_text = (result.stdout + result.stderr).strip().splitlines()
        version_text = version_text[0] if version_text else "version unknown"
        report_line("dcm2niix", "[OK]", f"{location}  ({version_text})")
        return True
    except Exception as error:
        # Found the file but could not run it -> report the problem clearly.
        report_line(
            "dcm2niix",
            "[BROKEN]",
            f"Found at {location} but failed to run: {error}",
        )
        return False


def check_python_library(module_name, requirement_label):
    """
    Check whether a Python library can be imported.

    module_name       : the import name, e.g. "pydicom"
    requirement_label : "RECOMMENDED" or "OPTIONAL", shown in the advice text.
    """
    try:
        module = importlib.import_module(module_name)
        # Many libraries expose their version as __version__; fall back if not.
        version_text = getattr(module, "__version__", "version unknown")
        report_line(module_name, "[OK]", f"version {version_text}")
        return True
    except ImportError:
        report_line(
            module_name,
            "[MISSING]",
            f"{requirement_label}. Install with:  pip install {module_name}",
        )
        return False


def main():
    print("=" * 70)
    print(" ENVIRONMENT CHECK  -  DICOM to NIfTI pipeline")
    print("=" * 70)
    print()

    # Run each check and remember whether the REQUIRED ones passed.
    python_ok   = check_python()
    dcm2niix_ok = check_dcm2niix()
    pydicom_ok  = check_python_library("pydicom", "RECOMMENDED")
    nibabel_ok  = check_python_library("nibabel", "OPTIONAL")

    print()
    print("-" * 70)

    # The pipeline cannot run at all without Python and dcm2niix.
    # pydicom is needed for the validation/provenance steps we will add later,
    # so we strongly recommend it, but it is not strictly required to convert.
    if python_ok and dcm2niix_ok and pydicom_ok:
        print(" RESULT: You are GOOD TO GO. All required tools are ready.")
    elif python_ok and dcm2niix_ok:
        print(" RESULT: Conversion will work, but install 'pydicom' before the")
        print("         validation/provenance steps (pip install pydicom).")
    else:
        print(" RESULT: NOT ready yet. Install the [MISSING] items above first,")
        print("         then run this script again.")

    print("-" * 70)
    print()

    # If dcm2niix is missing, print friendly install instructions. We only show
    # this when needed, to keep the output clean when everything is fine.
    if not dcm2niix_ok:
        print(" HOW TO INSTALL dcm2niix (Windows):")
        print(" ---------------------------------")
        print(" Easiest option - download the ready-made program:")
        print("   1. Go to:  https://github.com/rordenlab/dcm2niix/releases")
        print("   2. Download the file named like 'dcm2niix_win.zip'.")
        print("   3. Unzip it. Inside you will find 'dcm2niix.exe'.")
        print("   4. Put 'dcm2niix.exe' in a folder, e.g.  C:\\dcm2niix\\")
        print("   5. Add that folder to your Windows PATH so the command works")
        print("      from any terminal:")
        print("        - Press Start, type 'environment variables', open")
        print("          'Edit the system environment variables'.")
        print("        - Click 'Environment Variables...'")
        print("        - Under 'User variables', select 'Path' -> 'Edit'")
        print("        - Click 'New', paste  C:\\dcm2niix  , click OK on all.")
        print("   6. Close and reopen your terminal, then run this script again.")
        print()
        print(" Alternative (if you use conda):  conda install -c conda-forge dcm2niix")
        print()


# This standard line means: only run main() when the file is executed directly
# (e.g. 'python check_environment.py'), not when it is imported by another file.
if __name__ == "__main__":
    main()
