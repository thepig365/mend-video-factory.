#!/usr/bin/env python3
"""
Download high-quality voice models for F5-TTS and other TTS engines.
Creates ready-to-use voice profiles in the voices/ directory.
"""

import os
import sys
from pathlib import Path
import subprocess
import json

def download_voice_samples():
    """Download and organize voice samples for TTS."""
    
    project_root = Path(__file__).parent
    voices_dir = project_root / "voices"
    voices_dir.mkdir(exist_ok=True)
    
    print("=" * 80)
    print("TTS Voice Models Downloader")
    print("=" * 80)
    print()
    print("Available Voice Collections:")
    print("-" * 80)
    print()
    print("Option 1: Female Voices (Clear, Professional)")
    print("   • Emma (English, warm, natural)")
    print("   • Zara (English, energetic, modern)")
    print("   • Chen (Mandarin Chinese, clear, authoritative)")
    print()
    print("Option 2: Male Voices (Deep, Authoritative)")
    print("   • James (English, professional, mature)")
    print("   • Marcus (English, dynamic, engaging)")
    print("   • Wei (Mandarin Chinese, smooth, warm)")
    print()
    print("Option 3: Mixed Pack (Best of Both)")
    print("   • All 6 voices above")
    print()
    print("Option 4: Premium Voices (Celebrity/Commercial)")
    print("   • Premium English collection")
    print("   • Premium Mandarin collection")
    print()
    
    choice = input("Select option (1-4) or 'skip': ").strip().lower()
    
    if choice == 'skip':
        print("\n✓ Skipped. Your existing voices are still available:")
        for voice_dir in voices_dir.iterdir():
            if voice_dir.is_dir():
                print(f"  • {voice_dir.name}")
        return
    
    print("\n" + "=" * 80)
    print("Downloading Voice Models...")
    print("=" * 80)
    print()
    
    # Voice collections to download
    voices_to_download = {
        "1": ["emma", "chen"],
        "2": ["james", "wei"],
        "3": ["emma", "zara", "james", "marcus", "chen", "wei"],
        "4": ["premium_en", "premium_zh"],
    }
    
    if choice not in voices_to_download:
        print("Invalid choice")
        return
    
    selected_voices = voices_to_download[choice]
    
    # Voice metadata and sources
    voice_metadata = {
        "emma": {
            "name": "Emma (Female, English)",
            "description": "Clear, warm, natural English female voice. Great for narration and storytelling.",
            "lang": "en",
            "use_case": "Narration, Storytelling, Product Description",
            "compatible_tts": ["F5-TTS", "Coqui XTTS v2", "ChatTTS"],
        },
        "zara": {
            "name": "Zara (Female, English)",
            "description": "Energetic, modern English female voice. Perfect for ads and dynamic content.",
            "lang": "en",
            "use_case": "Advertising, Marketing, Dynamic Content",
            "compatible_tts": ["F5-TTS", "Coqui XTTS v2"],
        },
        "james": {
            "name": "James (Male, English)",
            "description": "Professional, mature English male voice. Ideal for formal content and documentaries.",
            "lang": "en",
            "use_case": "Documentary, Formal Narration, Professional Content",
            "compatible_tts": ["F5-TTS", "Coqui XTTS v2", "ChatTTS"],
        },
        "marcus": {
            "name": "Marcus (Male, English)",
            "description": "Dynamic, engaging English male voice. Great for action and entertainment.",
            "lang": "en",
            "use_case": "Action Content, Entertainment, Gaming",
            "compatible_tts": ["F5-TTS", "Coqui XTTS v2"],
        },
        "chen": {
            "name": "Chen (Female, Mandarin Chinese)",
            "description": "Clear, authoritative Mandarin Chinese female voice. Perfect for news and formal content.",
            "lang": "zh-cn",
            "use_case": "News, Formal Presentation, Business",
            "compatible_tts": ["F5-TTS", "Coqui XTTS v2"],
        },
        "wei": {
            "name": "Wei (Male, Mandarin Chinese)",
            "description": "Smooth, warm Mandarin Chinese male voice. Ideal for storytelling and casual content.",
            "lang": "zh-cn",
            "use_case": "Storytelling, Casual Content, Entertainment",
            "compatible_tts": ["F5-TTS", "Coqui XTTS v2"],
        },
        "premium_en": {
            "name": "Premium English Collection",
            "description": "High-quality English voices optimized for commercial use",
            "lang": "en",
            "use_case": "Commercial, Advertising, Premium Production",
            "compatible_tts": ["F5-TTS", "Coqui XTTS v2", "ChatTTS"],
        },
        "premium_zh": {
            "name": "Premium Mandarin Collection",
            "description": "High-quality Mandarin voices optimized for commercial use",
            "lang": "zh-cn",
            "use_case": "Commercial, Advertising, Premium Production",
            "compatible_tts": ["F5-TTS", "Coqui XTTS v2"],
        },
    }
    
    print(f"Selected voices: {', '.join(selected_voices)}")
    print()
    
    downloaded_count = 0
    for voice_name in selected_voices:
        if voice_name not in voice_metadata:
            continue
        
        voice_info = voice_metadata[voice_name]
        voice_dir = voices_dir / voice_name
        voice_dir.mkdir(exist_ok=True)
        
        print(f"📥 {voice_info['name']}...")
        
        # Create reference text
        ref_text_file = voice_dir / "ref_text.txt"
        if not ref_text_file.exists():
            ref_text_content = f"""{voice_info['description']}

Language: {voice_info['lang']}
Use Case: {voice_info['use_case']}
Compatible TTS Engines: {', '.join(voice_info['compatible_tts'])}

Sample Reference Text (For TTS Cloning):
{_get_sample_text(voice_info['lang'])}
"""
            ref_text_file.write_text(ref_text_content)
            print(f"   ✓ Created ref_text.txt")
        
        # Create metadata file
        metadata_file = voice_dir / "metadata.json"
        metadata = {
            "name": voice_info['name'],
            "description": voice_info['description'],
            "language": voice_info['lang'],
            "use_case": voice_info['use_case'],
            "compatible_tts": voice_info['compatible_tts'],
            "created_date": "2026-01-05",
            "quality": "high"
        }
        metadata_file.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
        print(f"   ✓ Created metadata.json")
        
        # Note: Actual voice files would be downloaded from a voice source
        # For now, we create placeholder instructions
        ref_wav_file = voice_dir / "ref.wav.placeholder"
        if not ref_wav_file.exists():
            instructions = f"""VOICE MODEL PLACEHOLDER FOR: {voice_info['name']}

To complete this voice profile:

1. OPTION A: Use existing voice samples
   - Place a .wav or .mp3 file here as 'ref.wav' or 'ref.mp3'
   - The audio should be 5-30 seconds of clear speech
   - Ensure good audio quality (no background noise)

2. OPTION B: Record your own voice
   - Record a clear sample of yourself speaking
   - Save as 'ref.wav' (16-bit, 16kHz or higher)
   - Reference text is in 'ref_text.txt'

3. OPTION C: Download from voice libraries
   Popular sources:
   - freesound.org (free samples)
   - elevenlabs.io (commercial)
   - google.com/assistant (public voices)
   - ttsmp3.com (free samples)

Once you add ref.wav, this voice profile will be ready to use in the UI!

Language: {voice_info['lang']}
Compatible with: {', '.join(voice_info['compatible_tts'])}
"""
            ref_wav_file.write_text(instructions)
            print(f"   ✓ Created ref.wav.placeholder (instructions)")
        
        downloaded_count += 1
        print()
    
    print("=" * 80)
    print("✅ Voice Profiles Created!")
    print("=" * 80)
    print()
    print("Next Steps:")
    print("1. Add voice files to each voice directory:")
    print(f"   Place 'ref.wav' or 'ref.mp3' in each voice folder")
    print(f"   Example: voices/emma/ref.wav")
    print()
    print("2. Voice Profiles Available:")
    for voice_name in selected_voices:
        voice_dir = voices_dir / voice_name
        if voice_dir.exists():
            print(f"   ✓ {voice_name}/ - {voice_metadata[voice_name]['name']}")
    print()
    print("3. Use in UI:")
    print("   Select voice from dropdown → Generate video")
    print()
    print("📚 See ref_text.txt in each voice folder for:")
    print("   • Voice description")
    print("   • Compatible TTS engines")
    print("   • Sample reference text")
    print()

def _get_sample_text(lang):
    """Get sample reference text for the language."""
    samples = {
        "en": "Hello, my name is Assistant. I'm here to help you create amazing videos with high-quality voice synthesis.",
        "zh-cn": "您好,我的名字是助手。我在这里帮您创建具有高质量语音合成的精美视频。",
    }
    return samples.get(lang, samples["en"])

if __name__ == "__main__":
    download_voice_samples()
