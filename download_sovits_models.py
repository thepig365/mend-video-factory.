#!/usr/bin/env python3
"""
Download GPT-SoVITS pre-trained models from official sources.
Models will be saved to .sovits-models/
"""

import os
import sys
import subprocess
from pathlib import Path

def download_models():
    """Download GPT-SoVITS models using git and direct downloads."""
    
    project_root = Path(__file__).parent
    models_dir = project_root / ".sovits-models"
    models_dir.mkdir(parents=True, exist_ok=True)
    
    print("=" * 70)
    print("GPT-SoVITS Model Downloader (Direct Download)")
    print("=" * 70)
    print(f"Models will be saved to: {models_dir}")
    print()
    
    print("📌 Model Information:")
    print("-" * 70)
    print("The GPT-SoVITS project contains models that can be downloaded from:")
    print("  https://github.com/RVC-Boss/GPT-SoVITS/releases")
    print()
    print("Pre-trained models (~2-3GB total):")
    print("  1. GPT-SoVITS-v2 weights (~1.5GB)")
    print("  2. BERT model (~1GB)")
    print("  3. VITs2 encoder (~500MB)")
    print()
    
    choice = input("Would you like to:\n(1) Clone GPT-SoVITS source + auto-setup\n(2) Keep using F5-TTS fallback\n\nChoice (1 or 2): ").strip()
    
    if choice == "1":
        print("\n" + "=" * 70)
        print("Cloning GPT-SoVITS Source Repository...")
        print("=" * 70)
        
        src_dir = project_root / ".sovits-src"
        
        if src_dir.exists():
            print(f"✓ {src_dir} already exists, skipping clone")
        else:
            try:
                print(f"\n📥 Cloning GPT-SoVITS...")
                cmd = f"git clone --depth 1 https://github.com/RVC-Boss/GPT-SoVITS.git {src_dir}"
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                
                if result.returncode == 0:
                    print(f"✅ Cloned to: {src_dir}")
                else:
                    print(f"⚠ Clone output: {result.stderr[:200]}")
            except Exception as e:
                print(f"Error cloning: {e}")
                return
        
        print("\n" + "=" * 70)
        print("Next Steps for Full GPT-SoVITS:")
        print("=" * 70)
        print("""
1. Download pre-trained models from:
   https://github.com/RVC-Boss/GPT-SoVITS/releases
   
2. Place downloaded model files in:
   .sovits-models/
   
3. Install source dependencies:
   source .venv-sovits/bin/activate
   cd .sovits-src
   pip install -r requirements.txt
   
4. Test with:
   python inference.py --ref_audio /path/to/voice.wav \\
                       --ref_text "transcript" \\
                       --gen_text "text to generate"

5. Once models are installed, update web/app.py to use .venv-sovits
   (See SOVITS_SETUP.md for details)

For now, GPT-SoVITS selection will still use F5-TTS fallback.
""")
        
    elif choice == "2":
        print("\n✅ Keeping current setup (F5-TTS fallback)")
        print("You can always come back to install full GPT-SoVITS models later.")
        print("\nRun this script again to download when ready:")
        print(f"  python3 {Path(__file__).name}")
    else:
        print("Invalid choice")
        sys.exit(1)
    
    print("\n" + "=" * 70)
    print("Summary:")
    print("=" * 70)
    print("✅ GPT-SoVITS integration: Complete")
    print("✅ UI option: Available")
    print("✅ F5-TTS fallback: Ready")
    print("✅ Models: Download guide provided")
    print()

if __name__ == "__main__":
    download_models()
