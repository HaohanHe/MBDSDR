# -*- coding: utf-8 -*-
"""Update device tests: assert absent unless real hardware present."""
import io

def patch(path, old, new):
    s = io.open(path, encoding="utf-8").read()
    assert s.count(old) == 1, path
    io.open(path, "w", encoding="utf-8", newline="").write(s.replace(old, new))

base = "/home/user/Doubao/chats/38438160041798146/tests/"

patch(base+"hackrf_params_test.py",
'''    def test_enumerate_includes_hackrf(self):
        from mbdsdr_ai.sdr_backend import enumerate_all_sdr_devices
        devs = enumerate_all_sdr_devices()
        hackrfs = [d for d in devs if d.get("driver") == "hackrf"]
        self.assertEqual(len(hackrfs), 1)
        d = hackrfs[0]
        self.assertEqual(d["freq_range"], (1e6, 6e9))
        self.assertEqual(d["sample_rate_range"], (2e6, 20e6))
        self.assertEqual(d["vga_gain_range"], (0.0, 62.0))
        self.assertEqual(d["txvga_gain_range"], (0.0, 47.0))''',
'''    def test_enumerate_absent_without_hardware(self):
        # 无真实 HackRF 时枚举不得包含 hackrf 条目（不再无条件列出假设备）
        from mbdsdr_ai.sdr_backend import enumerate_all_sdr_devices
        devs = enumerate_all_sdr_devices()
        self.assertEqual(
            [d for d in devs if d.get("driver") == "hackrf"], [])''')

patch(base+"bladerf_test.py",
'''    def test_enumerate_includes_bladerf(self):
        from mbdsdr_ai.sdr_backend import enumerate_all_sdr_devices
        devs = enumerate_all_sdr_devices()
        bladerfs = [d for d in devs if d.get("driver") == "bladerf"]
        self.assertEqual(len(bladerfs), 1)
        d = bladerfs[0]
        # bladerf2_common.h:550-555,518-523,542-547
        self.assertEqual(d["freq_range"], (70e6, 6e9))
        self.assertEqual(d["sample_rate_range"], (520834, 61_440_000))
        self.assertEqual(d["bandwidth_range"], (200_000, 56_000_000))
        # bladeRF1.h:154,160,166,172
        self.assertEqual(d["rxvga1_gain_range"], (5.0, 30.0))
        self.assertEqual(d["rxvga2_gain_range"], (0.0, 30.0))''',
'''    def test_enumerate_absent_without_hardware(self):
        # 无真实 bladeRF 时枚举不得包含 bladerf 条目（不再无条件列出假设备）
        from mbdsdr_ai.sdr_backend import enumerate_all_sdr_devices
        devs = enumerate_all_sdr_devices()
        self.assertEqual(
            [d for d in devs if d.get("driver") == "bladerf"], [])''')

patch(base+"limesuite_test.py",
'''    def test_enumerate_includes_limesdr(self):
        from mbdsdr_ai.sdr_backend import enumerate_all_sdr_devices
        devs = enumerate_all_sdr_devices()
        limesdrs = [d for d in devs if d.get("driver") == "limesdr"]
        self.assertEqual(len(limesdrs), 1)
        d = limesdrs[0]
        self.assertEqual(d["freq_range"], (1e5, 3.8e9))
        self.assertEqual(d["sample_rate_range"], (1e5, 61.44e6))
        self.assertEqual(d["gain_range"], (0.0, 73.0))
        self.assertEqual(d["lna_gain_range"], (0.0, 30.0))
        self.assertEqual(d["tia_gain_range"], (0.0, 12.0))''',
'''    def test_enumerate_absent_without_hardware(self):
        # 无真实 LimeSDR 时枚举不得包含 limesdr 条目（不再无条件列出假设备）
        from mbdsdr_ai.sdr_backend import enumerate_all_sdr_devices
        devs = enumerate_all_sdr_devices()
        self.assertEqual(
            [d for d in devs if d.get("driver") == "limesdr"], [])''')

print("device tests updated")
