# TouchWiz Access

TouchWiz Access is an experimental NVDA add-on for navigating Windows with a laptop's Precision Touchpad. It requires NVDA 2026.1 or later and a compatible touchpad. Gesture detection and the supported finger count can vary by device.

[Download the v0.3.10 preview](https://github.com/sarperarikan/touchwiz-access/releases/tag/v0.3.10) · [English guide](doc/en/readme.html) · [Türkçe kılavuz](doc/tr/readme.html)

## Getting started

1. Download and open `touchwiz-access.nvda-addon`, confirm installation, and restart NVDA.
2. Press `NVDA+Alt+Shift+T` to turn touchpad navigation on or off.
3. Turn your thumb and index finger as if opening a bottle cap to select an element type. Swipe right or left with one finger to move to the next or previous matching element.

Three-finger horizontal swipes cycle through open windows, a three-finger double tap copies, and a three-finger triple tap pastes. A single-finger double tap sends Enter on supported Windows 11 24H2 and later systems. Supported built-in Windows touchpad gestures are temporarily suspended while TouchWiz Access is active.

Open **NVDA → Preferences → TouchWiz Access** to configure gestures, create per-application profiles, or launch the gesture wizard. **General → Open user guide** opens the offline guide included with the add-on.

## Türkçe

TouchWiz Access, Windows dizüstü bilgisayarlarda dokunmatik yüzeyle gezinmek için geliştirilmiş deneysel bir NVDA eklentisidir. NVDA 2026.1 veya üzeri ve uyumlu bir Windows Precision Touchpad gerektirir. Hareket algılama ve desteklenen parmak sayısı cihaza göre değişebilir.

1. [Sürüm sayfasından](https://github.com/sarperarikan/touchwiz-access/releases/tag/v0.3.10) `touchwiz-access.nvda-addon` dosyasını indirip açın. Kurulumu onaylayıp NVDA’yı yeniden başlatın.
2. `NVDA+Alt+Shift+T` ile dokunmatik yüzeyle gezinmeyi açın veya kapatın.
3. Baş ve işaret parmağınızı şişe kapağı çevirir gibi döndürerek bir öğe türü seçin. Tek parmakla sağa ya da sola kaydırarak bu türdeki sonraki veya önceki öğeye gidin.

Üç parmakla yatay kaydırma açık pencereler arasında geçiş yapar; üç parmakla çift dokunma kopyalar, üç kez dokunma yapıştırır. Desteklenen Windows 11 24H2 ve üzeri sistemlerde tek parmakla çift dokunma Enter işlevi görür. TouchWiz açıkken Windows’un desteklenen yerleşik dokunmatik yüzey hareketleri geçici olarak duraklatılır.

**NVDA → Tercihler → TouchWiz Access** yolundan hareketleri özelleştirebilir, uygulama profilleri oluşturabilir ve hareket sihirbazını açabilirsiniz. Çevrimdışı kılavuza **Genel → Kullanım kılavuzunu aç** düğmesinden ulaşabilirsiniz.

## Building from source

Run these commands from the repository root with Python 3.

```sh
python -m pip install Babel
python build.py
```

The build writes `dist/touchwiz-access.nvda-addon`.

## Feedback and license

When [reporting an issue](https://github.com/sarperarikan/touchwiz-access/issues), include your touchpad or laptop model, Windows and NVDA versions, the application, the gesture, and the expected and actual behavior.

Developed by [Sarper Arıkan](https://sarperarikan.net). The project is licensed under the [GNU General Public License v2 or later](COPYING.txt).
