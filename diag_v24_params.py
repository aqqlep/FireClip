# 快速诊断v2.4参数问题
import os, sys
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT_DIR)

from config import CONFIG
pl = CONFIG.pipeline

print("=" * 60)
print("FireClip v2.4 采样间隔参数诊断")
print("=" * 60)

print(f"\n配置值:")
print(f"  max_sample_interval_sec (高运动区，小间隔=高fps): {pl.max_sample_interval_sec}s -> {1/pl.max_sample_interval_sec:.2f}fps")
print(f"  base_sample_interval_sec (基础，标准采样):           {pl.base_sample_interval_sec}s -> {1/pl.base_sample_interval_sec:.2f}fps")
print(f"  min_sample_interval_sec (资源紧张，大间隔=低fps):     {pl.min_sample_interval_sec}s -> {1/pl.min_sample_interval_sec:.2f}fps")

print(f"\n断言预期顺序: max < base < min")
print(f"  实际:       {pl.max_sample_interval_sec} < {pl.base_sample_interval_sec} < {pl.min_sample_interval_sec}")
print(f"  结果:       {'✓ 顺序正确' if pl.max_sample_interval_sec < pl.base_sample_interval_sec < pl.min_sample_interval_sec else '✗ 顺序错误'}")

print(f"\n其他关键资源参数:")
print(f"  memory_graceful={pl.memory_graceful_mb}MB < "
      f"memory_critical={pl.memory_critical_mb}MB < "
      f"memory_limit={pl.memory_limit_mb}MB -> "
      f"{'✓ OK' if pl.memory_graceful_mb < pl.memory_critical_mb < pl.memory_limit_mb else '✗ FAIL'}")

print(f"  cpu_graceful={pl.cpu_graceful_percent}% < "
      f"cpu_critical={pl.cpu_critical_percent}% < "
      f"cpu_limit={pl.cpu_limit_percent}% -> "
      f"{'✓ OK' if pl.cpu_graceful_percent < pl.cpu_critical_percent < pl.cpu_limit_percent else '✗ FAIL'}")

print(f"\n版本标识:")
print(f"  CONFIG.app_version = {CONFIG.app_version}")
print(f"  预期: v2.4")

print("\n" + "=" * 60)
