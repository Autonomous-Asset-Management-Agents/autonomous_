# check_cuda.py
# Quick check: is CUDA available for PyTorch (LSTM/RL training)?
# Run from "AI Trading Bot":  python scripts/check_cuda.py

import sys


def main():
    print("PyTorch / CUDA check")
    print("=" * 50)
    try:
        import torch

        print("PyTorch version:", torch.__version__)
        cuda_available = torch.cuda.is_available()
        print("CUDA available:", cuda_available)
        if cuda_available:
            print("CUDA version (runtime):", torch.version.cuda)
            print("Device count:", torch.cuda.device_count())
            for i in range(torch.cuda.device_count()):
                print("  Device %d: %s" % (i, torch.cuda.get_device_name(i)))
            print(
                "Current device: %s"
                % torch.cuda.get_device_name(torch.cuda.current_device())
            )
        else:
            print(
                "(Training will use CPU. Install CUDA build of PyTorch for GPU: https://pytorch.org)"
            )
        print("=" * 50)
        return 0 if cuda_available else 1
    except ImportError as e:
        print("PyTorch not installed:", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
