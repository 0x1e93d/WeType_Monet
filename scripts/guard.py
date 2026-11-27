"""关闭微信输入法 env_stat 的杀进程与自毁逻辑。"""

from __future__ import annotations

import bisect
import io
import struct
import sys
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from scripts.paths import BuildPaths

TARGET_LIB_NAME = "libwxhld.so"
RET_ARM64 = b"\xc0\x03\x5f\xd6"
KILL_SYMBOLS = frozenset({"kill", "tgkill", "killpg"})
SCAN_SYMBOLS = frozenset({"opendir", "readdir", "closedir"})
CRASH_SYMBOLS = frozenset({"abort", "raise"})
WANTED_SYMBOLS = KILL_SYMBOLS | SCAN_SYMBOLS | CRASH_SYMBOLS


@dataclass(frozen=True)
class GuardPatch:
    path: Path
    arcname: str
    offset: int
    size: int
    calls: tuple[str, ...] = ()


def _sign_extend(value: int, bits: int) -> int:
    sign = 1 << (bits - 1)
    return (value & (sign - 1)) - (value & sign)


def _word_at(blob: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<I", blob, offset)[0]


def _decode_adrp(word: int, pc: int) -> int | None:
    if (word & 0x9F000000) != 0x90000000:
        return None
    immlo = (word >> 29) & 0x3
    immhi = (word >> 5) & 0x7FFFF
    immediate = _sign_extend((immhi << 2) | immlo, 21) << 12
    return (pc & ~0xFFF) + immediate


def _decode_ldr_uimm(word: int) -> int | None:
    if (word & 0xFFC00000) != 0xF9400000:
        return None
    return ((word >> 10) & 0xFFF) * 8


def _looks_like_prologue(word: int) -> bool:
    if (word & 0xFFC00000) == 0xA9800000:
        return True
    return (word & 0xFF800000) == 0xD1000000 and ((word >> 5) & 0x1F) == 31 and (word & 0x1F) == 31


def _plt_symbol_map(elf) -> dict[int, str]:
    relocations: dict[int, str] = {}
    for section_name in (".rela.plt", ".rela.dyn"):
        section = elf.get_section_by_name(section_name)
        if section is None:
            continue
        symbol_table = elf.get_section(section["sh_link"])
        for relocation in section.iter_relocations():
            if relocation["r_info_sym"]:
                relocations[relocation["r_offset"]] = symbol_table.get_symbol(relocation["r_info_sym"]).name

    plt = elf.get_section_by_name(".plt")
    result: dict[int, str] = {}
    if plt is None:
        return result
    start = plt["sh_addr"]
    data = plt.data()
    for index in range(0, len(data) - 15, 16):
        stub = start + index
        page = _decode_adrp(_word_at(data, index), stub)
        if page is None:
            continue
        offset = _decode_ldr_uimm(_word_at(data, index + 4))
        if offset is None:
            continue
        symbol = relocations.get(page + offset)
        if symbol:
            result[stub] = symbol
    return result


def _function_ranges(elf) -> list[tuple[int, int]]:
    from elftools.dwarf.callframe import CallFrameInfo, FDE
    from elftools.dwarf.structs import DWARFStructs

    section = elf.get_section_by_name(".eh_frame")
    if section is None:
        return []
    data = section.data()
    structs = DWARFStructs(little_endian=True, dwarf_format=64, address_size=8)
    # 兼容不同版本的 pyelftools
    try:
        info = CallFrameInfo(io.BytesIO(data), len(data), section["sh_addr"], structs, for_eh_frame=True)
    except TypeError:
        info = CallFrameInfo(io.BytesIO(data), len(data), section["sh_addr"], structs)

    entries = getattr(info, "entries", None)
    if entries is None:
        getter = getattr(info, "get_entries", None)
        entries = getter() if getter is not None else info._parse_entries()

    ranges: list[tuple[int, int]] = []
    for entry in entries:
        if not isinstance(entry, FDE):
            continue
        header = entry.header
        if "initial_location" in header:
            begin, size = header["initial_location"], header["address_range"]
        else:
            begin, size = header["pc_begin"], header["pc_range"]
        if begin and size:
            ranges.append((begin, begin + size))
    ranges.sort()
    return ranges


def _collect_calls(
    blob: bytearray,
    elf,
    plt_symbols: dict[int, str],
    ranges: list[tuple[int, int]],
) -> dict[tuple[int, int], set[str]]:
    text = elf.get_section_by_name(".text")
    if text is None or not text["sh_size"] or not ranges:
        return {}
    start, size, offset = text["sh_addr"], text["sh_size"], text["sh_offset"]
    raw = bytes(blob[offset : offset + size])
    words = array("I")
    words.frombytes(raw[: len(raw) // 4 * 4])
    if sys.byteorder != "little":
        words.byteswap()

    targets = {address: name for address, name in plt_symbols.items() if name in WANTED_SYMBOLS}
    starts = [item[0] for item in ranges]
    calls: dict[tuple[int, int], set[str]] = {}
    for index, word in enumerate(words):
        if (word & 0xFC000000) != 0x94000000:
            continue
        address = start + index * 4
        immediate = word & 0x3FFFFFF
        if immediate & 0x2000000:
            immediate -= 0x4000000
        symbol = targets.get(address + (immediate << 2))
        if symbol is None:
            continue
        position = bisect.bisect_right(starts, address) - 1
        if position < 0 or address >= ranges[position][1]:
            continue
        calls.setdefault(ranges[position], set()).add(symbol)
    return calls


def select_guard_functions(
    calls: dict[tuple[int, int], set[str]],
) -> list[tuple[tuple[int, int], set[str]]]:
    killers = {span: symbols for span, symbols in calls.items() if symbols & KILL_SYMBOLS}
    if not killers:
        return []
    scanners = {span: symbols for span, symbols in killers.items() if symbols & SCAN_SYMBOLS}
    if not scanners:
        detail = "; ".join(
            f"{span[0]:#x}-{span[1]:#x}=[{','.join(sorted(symbols))}]" for span, symbols in sorted(killers.items())
        )
        raise RuntimeError(f"[!] 检测到 kill 调用，但无法确定自毁函数: {detail}")
    with_crash = {span: symbols for span, symbols in scanners.items() if symbols & CRASH_SYMBOLS}
    return sorted((with_crash or scanners).items())


def apply_ret_patch(path: Path, offset: int) -> bool:
    data = bytearray(path.read_bytes())
    if offset < 0 or offset + 4 > len(data):
        raise ValueError(f"补丁偏移越界: {offset:#x}")
    if bytes(data[offset : offset + 4]) == RET_ARM64:
        return False
    data[offset : offset + 4] = RET_ARM64
    path.write_bytes(bytes(data))
    return True


def patch_library(path: Path) -> list[GuardPatch]:
    from elftools.elf.elffile import ELFFile

    blob = bytearray(path.read_bytes())
    elf = ELFFile(io.BytesIO(bytes(blob)))
    calls = _collect_calls(blob, elf, _plt_symbol_map(elf), _function_ranges(elf))
    chosen = select_guard_functions(calls)
    if not chosen:
        print(f"[*] {path.name}: 未发现进程查杀逻辑，跳过")
        return []

    patches: list[GuardPatch] = []
    mutated = False
    for (begin, end), symbols in chosen:
        if bytes(blob[begin : begin + 4]) == RET_ARM64:
            print(f"[*] {path.name}: 自毁函数入口 {begin:#x} 已是 ret，跳过")
        else:
            if not _looks_like_prologue(_word_at(blob, begin)):
                raise RuntimeError(f"[!] {path.name}: {begin:#x} 入口不是预期函数序言，终止构建")
            blob[begin : begin + 4] = RET_ARM64
            mutated = True
            print(f"[+] {path.name}: 已关闭签名校验自毁逻辑")
            print(f"    函数范围 {begin:#x}-{end:#x}（{end - begin:#x} 字节），入口写入 ret")
            print(f"    命中依据 {', '.join(sorted(symbols))}")
        patches.append(GuardPatch(path=path, arcname="", offset=begin, size=end - begin, calls=tuple(sorted(symbols))))
    if mutated:
        path.write_bytes(bytes(blob))
    return patches


def patch_decoded_tree(paths: BuildPaths, apk_code: str | None = None) -> list[GuardPatch]:
    libs = sorted(paths.decompile_dir.glob(f"lib/*/{TARGET_LIB_NAME}"))
    if not libs:
        raise RuntimeError(f"[!] 解码目录缺少 {TARGET_LIB_NAME}: {paths.decompile_dir}/lib")
    if apk_code:
        print(f"[*] 检查 WeType {apk_code} 的签名校验自毁逻辑...")
    results: list[GuardPatch] = []
    for lib in libs:
        arcname = lib.relative_to(paths.decompile_dir).as_posix()
        for patch in patch_library(lib):
            results.append(GuardPatch(patch.path, arcname, patch.offset, patch.size, patch.calls))
    if results:
        names = ", ".join(sorted({item.arcname for item in results}))
        print(f"[+] 补丁完成: {names}，共 {len(results)} 处")
    return results


def verify_patched_apk(apk_path: Path, patches: Iterable[GuardPatch]) -> None:
    import zipfile

    patch_list = list(patches)
    with zipfile.ZipFile(apk_path) as archive:
        for patch in patch_list:
            data = archive.read(patch.arcname)
            if bytes(data[patch.offset : patch.offset + 4]) != RET_ARM64:
                raise RuntimeError(
                    f"[!] 补丁校验失败: {apk_path.name} 中的 {patch.arcname} 偏移 {patch.offset:#x} 不是 ret"
                )
    if patch_list:
        print(f"[+] 补丁校验通过: {apk_path.name}（{len(patch_list)} 处）")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="关闭微信输入法 env_stat 自毁")
    parser.add_argument("target", type=Path, help="解码目录或 libwxhld.so")
    args = parser.parse_args(argv)

    if args.target.is_dir():
        libs = sorted(args.target.glob(f"lib/*/{TARGET_LIB_NAME}"))
        if not libs:
            raise SystemExit(f"[!] 目录下没有 lib/*/{TARGET_LIB_NAME}: {args.target}")
    else:
        libs = [args.target]

    patched = sum(1 for lib in libs if patch_library(lib))
    print(f"[+] 检查 {len(libs)} 个 so，写入补丁 {patched} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
