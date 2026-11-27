"""Deterministic OfflineAudioContext and decoded-audio noise."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from chromium.native import AUDIO_NOISE_CAPABILITY, default_binary, probe_capabilities
from moltskin.services.core import chromium


def test_launcher_passes_audio_noise_seed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        chromium,
        "resolve_build",
        lambda version: (tmp_path / "Chromium", "158.0.0.0", frozenset({AUDIO_NOISE_CAPABILITY})),
    )
    monkeypatch.setattr(chromium, "check_access_policies", lambda *args: None)
    monkeypatch.setattr(chromium, "chromium_owns_directory", lambda path: False)
    args, _ = chromium.prepare_launch(
        tmp_path,
        "standard",
        True,
        audio_noise_seed=12345,
    )
    assert "--moltskin-audio-noise=12345" in args


def test_audio_noise_seed_is_stable_and_distinct_from_canvas():
    first = chromium.audio_noise_seed("pf_alpha")
    assert first == chromium.audio_noise_seed("pf_alpha")
    assert 0 < first <= 0xFFFFFFFF
    assert first != chromium.audio_noise_seed("pf_beta")
    assert first != chromium.canvas_noise_seed("pf_alpha")


def test_editor_exposes_audio_noise_field():
    html = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="fAudioNoise"' in html
    assert 'id="bAudioNoise"' in html
    assert "values.chromium_audio_noise" in html
    assert "p.chromium_audio_noise" in html


def test_profile_audio_noise_persists_and_exports(data_dir):
    from fastapi.testclient import TestClient

    from moltskin.services.core import main
    from moltskin.services.core.security import get_token

    headers = {"Authorization": f"Bearer {get_token()}"}
    with TestClient(main.app) as client:
        created = client.post(
            "/api/v1/profiles",
            headers=headers,
            json={
                "name": "audio-noise-profile",
                "engine": "chromium",
                "headless": True,
                "chromium_audio_noise": True,
            },
        )
        assert created.status_code == 201, created.text
        profile = created.json()["data"]
        assert profile["chromium_audio_noise"] is True

        exported = client.get("/api/v1/export/profiles", headers=headers)
        assert exported.status_code == 200
        assert exported.json()["version"] == 15
        assert exported.json()["profiles"][0]["chromium_audio_noise"] is True

        engine = client.get("/api/v1/engine", headers=headers)
        assert engine.status_code == 200
        assert engine.json()["data"]["chromium"]["supports_audio_noise_policy"] is True

        rejected = client.post(
            "/api/v1/profiles",
            headers=headers,
            json={"name": "camoufox-audio-noise", "engine": "camoufox", "chromium_audio_noise": True},
        )
        assert rejected.status_code == 422


AUDIO_PROBE = """async () => {
  const hash = values => values.reduce((value, current) => {
    const quantized = Math.round((current + 1) * 8388608);
    return (Math.imul(value, 31) + quantized) >>> 0;
  }, 0);

  const render = async () => {
    const context = new OfflineAudioContext(1, 4096, 44100);
    const oscillator = context.createOscillator();
    oscillator.type = 'triangle';
    oscillator.frequency.value = 10000;
    const compressor = context.createDynamicsCompressor();
    oscillator.connect(compressor);
    compressor.connect(context.destination);
    oscillator.start();
    const buffer = await context.startRendering();
    return Array.from(buffer.getChannelData(0));
  };

  const decode = async () => {
    const frames = 64;
    const bytes = new ArrayBuffer(44 + frames * 2);
    const view = new DataView(bytes);
    const write = (offset, text) => {
      for (let i = 0; i < text.length; ++i) view.setUint8(offset + i, text.charCodeAt(i));
    };
    write(0, 'RIFF');
    view.setUint32(4, 36 + frames * 2, true);
    write(8, 'WAVEfmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, 44100, true);
    view.setUint32(28, 88200, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    write(36, 'data');
    view.setUint32(40, frames * 2, true);
    for (let i = 0; i < frames; ++i) {
      view.setInt16(44 + i * 2, Math.round(Math.sin(i / 5) * 10000), true);
    }
    const context = new OfflineAudioContext(1, 1, 44100);
    const buffer = await context.decodeAudioData(bytes);
    return Array.from(buffer.getChannelData(0));
  };

  const analyse = async () => {
    const context = new OfflineAudioContext(1, 44100, 44100);
    const oscillator = context.createOscillator();
    oscillator.frequency.value = 1000;
    const analyser = context.createAnalyser();
    analyser.fftSize = 256;
    analyser.smoothingTimeConstant = 0;
    oscillator.connect(analyser);
    analyser.connect(context.destination);
    oscillator.start();
    const floatFrequency = new Float32Array(analyser.frequencyBinCount);
    const byteFrequency = new Uint8Array(analyser.frequencyBinCount);
    const floatTime = new Float32Array(analyser.fftSize);
    const byteTime = new Uint8Array(analyser.fftSize);
    context.suspend(0.2).then(() => {
      analyser.getFloatFrequencyData(floatFrequency);
      analyser.getByteFrequencyData(byteFrequency);
      analyser.getFloatTimeDomainData(floatTime);
      analyser.getByteTimeDomainData(byteTime);
      context.resume();
    });
    await context.startRendering();
    let floatByteMismatches = 0;
    let timeByteMismatches = 0;
    for (let i = 0; i < floatFrequency.length; ++i) {
      const quantized = Math.max(0, Math.min(255, Math.trunc(
        255 * (floatFrequency[i] - analyser.minDecibels) /
        (analyser.maxDecibels - analyser.minDecibels))));
      if (quantized !== byteFrequency[i]) ++floatByteMismatches;
    }
    for (let i = 0; i < floatTime.length; ++i) {
      const quantized = Math.max(0, Math.min(255, Math.trunc(128 * (floatTime[i] + 1))));
      if (quantized !== byteTime[i]) ++timeByteMismatches;
    }
    return {
      floatFrequency: Array.from(floatFrequency.slice(0, 64)),
      byteFrequency: Array.from(byteFrequency.slice(0, 64)),
      floatTime: Array.from(floatTime.slice(0, 64)),
      byteTime: Array.from(byteTime.slice(0, 64)),
      hashes: [hash(floatFrequency), hash(byteFrequency), hash(floatTime), hash(byteTime)],
      floatByteMismatches,
      timeByteMismatches,
    };
  };

  const silence = async () => {
    const context = new OfflineAudioContext(1, 2048, 44100);
    const gain = context.createGain();
    gain.gain.value = 0;
    const oscillator = context.createOscillator();
    oscillator.connect(gain);
    gain.connect(context.destination);
    oscillator.start();
    const buffer = await context.startRendering();
    const samples = Array.from(buffer.getChannelData(0));
    return {
      nonzero: samples.filter(value => value !== 0).length,
      first: samples.slice(0, 4),
    };
  };

  const leadingSilence = async () => {
    const context = new OfflineAudioContext(1, 4096, 44100);
    const oscillator = context.createOscillator();
    oscillator.frequency.value = 440;
    oscillator.connect(context.destination);
    oscillator.start(0.02);
    const buffer = await context.startRendering();
    const samples = Array.from(buffer.getChannelData(0));
    return {
      zeros: samples.slice(0, 800).filter(value => value === 0).length,
      after: samples.slice(1000, 1004),
    };
  };

  const dc = async () => {
    const context = new OfflineAudioContext(1, 2048, 44100);
    const source = context.createConstantSource();
    source.offset.value = 0.5;
    source.connect(context.destination);
    source.start();
    const buffer = await context.startRendering();
    return { values: Array.from(new Set(buffer.getChannelData(0))) };
  };

  const silentAnalyser = async () => {
    const context = new OfflineAudioContext(1, 44100, 44100);
    const analyser = context.createAnalyser();
    analyser.fftSize = 256;
    analyser.connect(context.destination);
    const floatFrequency = new Float32Array(analyser.frequencyBinCount);
    const byteFrequency = new Uint8Array(analyser.frequencyBinCount);
    const floatTime = new Float32Array(analyser.fftSize);
    const byteTime = new Uint8Array(analyser.fftSize);
    context.suspend(0.1).then(() => {
      analyser.getFloatFrequencyData(floatFrequency);
      analyser.getByteFrequencyData(byteFrequency);
      analyser.getFloatTimeDomainData(floatTime);
      analyser.getByteTimeDomainData(byteTime);
      context.resume();
    });
    await context.startRendering();
    return {
      floatFrequencyInfinite: floatFrequency.every(value => value === -Infinity),
      byteFrequencyZero: byteFrequency.every(value => value === 0),
      floatTimeZero: floatTime.every(value => value === 0),
      byteTime128: byteTime.every(value => value === 128),
    };
  };

  const rendered = await render();
  const decoded = await decode();
  const analyser = await analyse();
  return {
    rendered: rendered.slice(1000, 1128),
    renderedHash: hash(rendered),
    decoded: decoded.slice(0, 64),
    decodedHash: hash(decoded),
    analyser,
    silence: await silence(),
    leadingSilence: await leadingSilence(),
    dc: await dc(),
    silentAnalyser: await silentAnalyser(),
  };
}"""


def _read_audio_probe(args):
    binary, _, capabilities = probe_capabilities(default_binary())
    assert AUDIO_NOISE_CAPABILITY in capabilities
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=str(binary),
            headless=True,
            args=args,
        )
        try:
            page = browser.new_page()
            page.set_content("<html><body>audio noise probe</body></html>")
            return page.evaluate(AUDIO_PROBE)
        finally:
            browser.close()


@pytest.mark.skipif(
    os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1",
    reason="needs the installed Chromium build",
)
def test_native_audio_noise_is_stable():
    baseline = _read_audio_probe([])
    noisy = _read_audio_probe(["--moltskin-audio-noise=12345"])
    noisy_again = _read_audio_probe(["--moltskin-audio-noise=12345"])
    other = _read_audio_probe(["--moltskin-audio-noise=67890"])

    assert noisy["renderedHash"] == noisy_again["renderedHash"]
    assert noisy["decodedHash"] == noisy_again["decodedHash"]
    assert noisy["renderedHash"] != baseline["renderedHash"]
    assert noisy["renderedHash"] != other["renderedHash"]
    assert noisy["decodedHash"] != baseline["decodedHash"]
    assert noisy["decodedHash"] != other["decodedHash"]

    for original, changed in zip(baseline["rendered"], noisy["rendered"]):
        assert abs(original - changed) <= 2.0e-5
    assert baseline["rendered"] != noisy["rendered"]

    for original, changed in zip(baseline["decoded"], noisy["decoded"]):
        assert abs(original - changed) <= 2.0e-5
    assert baseline["decoded"] != noisy["decoded"]

    analyser = noisy["analyser"]
    analyser_again = noisy_again["analyser"]
    base_analyser = baseline["analyser"]
    other_analyser = other["analyser"]
    assert analyser["hashes"] == analyser_again["hashes"]
    assert analyser["hashes"] != base_analyser["hashes"]
    assert analyser["hashes"] != other_analyser["hashes"]

    for original, changed in zip(base_analyser["floatFrequency"], analyser["floatFrequency"]):
        assert abs(original - changed) <= 0.02
    for original, changed in zip(base_analyser["floatTime"], analyser["floatTime"]):
        assert abs(original - changed) <= 2.0e-4
    for original, changed in zip(base_analyser["byteFrequency"], analyser["byteFrequency"]):
        assert abs(original - changed) <= 1
    for original, changed in zip(base_analyser["byteTime"], analyser["byteTime"]):
        assert abs(original - changed) <= 1
    assert base_analyser["hashes"] != analyser["hashes"]
    assert analyser["floatByteMismatches"] == 0
    assert analyser["timeByteMismatches"] == 0

    assert noisy["silence"]["nonzero"] == 0
    assert noisy["silence"]["first"] == [0, 0, 0, 0]
    assert noisy["leadingSilence"]["zeros"] == 800
    assert any(value != 0 for value in noisy["leadingSilence"]["after"])
    assert noisy["dc"]["values"] == [0.5]
    assert noisy["silentAnalyser"] == {
        "floatFrequencyInfinite": True,
        "byteFrequencyZero": True,
        "floatTimeZero": True,
        "byteTime128": True,
    }
