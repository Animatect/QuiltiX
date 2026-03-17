print("Initializing QuiltiX...")
import os
import sys

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
venv_site_packages = os.path.join(root, ".venv", "Lib", "site-packages")
pxr_bin = os.path.join(venv_site_packages, "pxr", "bin")
pxr_lib = os.path.join(venv_site_packages, "pxr", "lib")

# Ensure USD DLLs are findable on Windows
os.environ["PATH"] = pxr_bin + os.pathsep + pxr_lib + os.pathsep + os.environ.get("PATH", "")

# Set USD tool paths
os.environ["USDVIEW"] = os.path.join(pxr_bin, "usdview")

# Check for Arnold render delegate
arnold_base = os.path.join(root, "delegates", "Arnold")
if os.path.isdir(arnold_base):
    sdk_candidates = [d for d in os.listdir(arnold_base) if d.startswith("Arnold-") and d.endswith("-windows")]
    if sdk_candidates:
        arnold_sdk = os.path.join(arnold_base, sdk_candidates[0])
        print(f"Found Arnold SDK: {arnold_sdk}")
        os.environ["PATH"] = os.path.join(arnold_sdk, "bin") + os.pathsep + os.environ["PATH"]
        arnold_plugin = os.path.join(arnold_base, "hdArnold", "plugin")
        os.environ["PXR_PLUGINPATH_NAME"] = arnold_plugin + os.pathsep + os.getenv("PXR_PLUGINPATH_NAME", "")

print("Loading QuiltiX...")
from QuiltiX import quiltix

print("Opening QuiltiX Editor...")
quiltix.launch()
