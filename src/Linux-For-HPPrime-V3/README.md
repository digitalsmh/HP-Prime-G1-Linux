# Linux-For-HPPrime-V3 (HP Prime G1 `G8X92AA` / `S3C2416`)

HP Prime G1 (`G8X92AA`, Hardware Rev A/C, Samsung `S3C2416` ARM926EJ-S) 계산기용 임베디드 리눅스 부트로더 및 Linux 네이티브 이미지/플래싱 툴체인 **V3** 구현체입니다.

- **상세 리버스 엔지니어링 분석 및 구현계획서**: 01_doc/01_구현계획서-v2.md
- **원본 레퍼런스**: [20_Repo/G1-Hardware/Linux-For-HPPrime-V2](https://github.com/Repeerc/Linux-For-HPPrime-V2)

---

## 1. V2 대비 주요 개선 사항

1. **NAND 플래시 오프셋 및 파티션 매핑 완전 규명 ([tools/nand_layout.py](./tools/nand_layout.py))**:
   - `BXCBOOT0.BIN` 복구 모드(Recovery Mode)의 USB Mass Storage 베이스 오프셋 시프트(`+0x00040000` = 256 KiB = 물리 Block 2)를 코드로 반영하여, `usbtool.ini` 오프셋(`0x00000000`, `0x000C0000`)과 `boot.c`/`mach-hpprime.c` 물리 오프셋(`0x00040000`, `0x00100000`) 간의 관계를 자동 변환·검증합니다.
2. **2차 부트로더 버그 수정 (bootloader/boot.c)**:
   - `powu32(2, sdiv)` 무한루프/지수계산 오류를 `(1U << sdiv)` 비트 시프트로 수정했습니다.
   - `set_flash_lock_boundary(10, 2040)`로 인해 커널 파티션(`mtd2`, Block 8~71)의 앞단 2개 블록(Block 8~9)이 쓰기 잠금되던 문제를 `set_flash_lock_boundary(8, 2040)`으로 바로잡았습니다.
   - 불필요한 더미 페이지 읽기(`page 4096`) 및 중복된 `rootwait` 커널 인자를 정리했습니다.
3. **고속·안전 이미지 빌더 및 분석기 ([tools/mkimg_v3.py](./tools/mkimg_v3.py))**:
   - `BOOT1.ROM`의 32바이트 헤더(`0x30000020`, `"V5J\0"`, `"2416"`, 마지막 바이트 `0xAA`), `zImage`(매직 `0x016F2818`, 최대 8 MiB), `rootfs.jffs2`(매직 `0x1985`, 최대 54 MiB) 유효성 및 파티션 침범 여부를 사전 검증합니다.
4. **Linux 네이티브 `SG_IO` 플래싱 및 프로토콜 도구 ([tools/usbtool_v3.py](./tools/usbtool_v3.py))**:
   - Windows 전용 `usbtool.exe` 없이 Linux에서 `usbtool.ini`를 파싱하여 멀티 오프셋 플래싱을 수행합니다.
   - 기본적으로 **안전한 `--dry-run` 시뮬레이션 모드**로 동작하여 실물 계산기에 실수로 덮어쓰는 것을 방지합니다.

### 1.1. 정정 메모 (2026-11-17, S3a 검토 → S3b 구현, 실물 미검증)

위 2·3항 설명 가운데 다음이 틀렸거나 빠져 있어 `bootloader/`와 `tools/mkimg_v3.py`를 고쳤습니다. 근거는 `30_TEST/40_Build/boot1/S3a_bootloader-review.md`입니다.

- BOOT1 헤더는 `+0x00 0x30000020, +0x08 0x100000, +0x0C 0x30000000, +0x10 0x100000, +0x14 "V5J\0", +0x18 "2416"`입니다(V2·순정과 같음). 예전 V3 배치(`+0x10 "V5J"`, 적재 주소 0)로는 Boot0가 BOOT1을 실행하지 않습니다. `mkimg_v3.py`도 이 배치로 검사합니다.
- MACH_TYPE은 1686(HPPRIMEV2), 스택은 `0x30007F00`(커널 적재 범위 밖), 클록은 V2 값(MPLL PDIV bit5, ARM 400/HCLK 133/PCLK 66 MHz), ATAG는 BSS 배열·MEM 32 MiB − 512 KiB, LCD는 FB `0x31F80000` 24 bpp(Boot0 타이밍 유지), NAND 잠금 레지스터는 행(페이지) 주소(`블록×64`)로 씁니다.
- libc `vsnprintf` 대신 작은 자체 포맷터를 씁니다(`_sbrk` 불필요). CMake는 `-nostartfiles -static -Wl,--gc-sections -lc_nano`로 링크합니다.

---

### 1.2. 실물 부팅 확인 (2026-11-18, G8X92AA Rev C, 복구 화면 v16)

- **V3 BOOT1 + LINUX.DAT로 Linux가 셸(`~#`)까지 부팅됩니다.** RNDIS(`192.168.66.1`)와 SSH(dropbear, root)가 동작합니다. Boot0(블록 0~1)는 한 번도 쓰지 않았습니다.
- 부팅을 막던 원인: BOOT1 명령줄의 값 없는 `earlycon`. ATAG 부팅(DTB 없음)에서 커널 6.1.35가 NULL fdt를 읽다가 `parse_early_param`에서 멈췄습니다. `bootloader/boot.c`에서 `earlycon`을 뺐고, 커널에도 NULL 검사를 넣었습니다.
- 커널 패치: [kernel/patches/](kernel/patches/) — `0001`(상시: fdt NULL 검사, Boot0·NAND 전체 mtd 읽기 전용), `0002`(디버그: 화면 진행 띠). 기준 소스는 `20_Repo/G1-Hardware/Kernel-6.1.35-HP-Prime-V2_G1`.
- 확인된 해시: BOOT1 `f1dca535…be30`, zImage(0001만) `f53221c0…b0aa`, LINUX.DAT `76ce1d24…ce56`.
- 경과 보고: 작업공간 `01_doc/20_Linux-Boot-과정결과.md`.
- RootFS overlay: [rootfs/](rootfs/) — 이름 기반 스왑, `hpp-dump`, Doom(`hpp-doom`, 계산기 키 배치). 2026-11-19 실물 확인(LINUX.DAT `c25999e2…`).

## 2. 주요 사용법

### 2.1. 연결된 HP Prime USB 상태 확인 (읽기 전용)
```bash
python3 tools/usbtool_v3.py status
```

### 2.2. 아티팩트(`BOOT1.ROM`, `zImage`, `rootfs.jffs2`) 검사
```bash
python3 tools/mkimg_v3.py inspect \
    ../../20_Repo/G1-Hardware/Linux-For-HPPrime-V2/BOOT1.ROM \
    ../../20_Repo/G1-Hardware/Linux-For-HPPrime-V2/zImage \
    ../../20_Repo/G1-Hardware/Linux-For-HPPrime-V2/rootfs.jffs2
```

### 2.3. `LINUX.DAT` 통합 이미지 빌드
```bash
python3 tools/mkimg_v3.py build \
    --boot1 ../../20_Repo/G1-Hardware/Linux-For-HPPrime-V2/BOOT1.ROM \
    --zimage ../../20_Repo/G1-Hardware/Linux-For-HPPrime-V2/zImage \
    --rootfs ../../20_Repo/G1-Hardware/Linux-For-HPPrime-V2/rootfs.jffs2 \
    --output LINUX.DAT
```

### 2.4. `usbtool.ini` 분석 및 플래싱 시뮬레이션 (`--dry-run`)
```bash
python3 tools/usbtool_v3.py parse-ini --ini usbtool.ini
python3 tools/usbtool_v3.py flash --ini usbtool.ini
```

### 2.5. 단위 테스트 실행
```bash
python3 -m unittest discover -s tests -v
```

### 2.6. 기록기 안전 규칙과 실물 기록 (S5, 2026-11-17)

`flash`는 장치를 열기 전에 모든 이미지를 `tools/write_plan.py`로 판정합니다. 이 파일은 `20_HPPrime-Tool/hpprime/layout.py`의 복사본입니다. 다음 중 하나라도 해당하면 거부합니다.

- 물리 블록 0~1(Boot0)에 닿는 경우
- LBA ≥ `0x7FE00`이거나 끝이 `0x3EC0000`을 넘는 경우
- 시작 오프셋이 128 KiB 경계가 아닌 경우
- 이미지끼리 겹치는 경우
- Boot0 이미지인 경우(`BXCBOOT0*.BIN` 이름 또는 알려진 sha256)

WRITE_10은 블록 안에서만 보냅니다. 각 블록의 첫 조각은 page 0에서 시작하고, 4섹터 단위이며, 꼬리는 0xFF로 채웁니다. 조각 하나는 256섹터(128 KiB) 이하입니다.

보내는 명령은 다음뿐이며, 이 순서로 보냅니다(S6부터 제어는 vendor `0x88`/`0x89` 경로). 제어 명령마다 `0x88`(264 B 패킷)을 보내고 `0x89`(264 B 응답)를 받아 확인합니다. 응답이 다르면 재시도 없이 멈추고 잠금을 한 번 시도합니다.

1. `0xBECD/0xBEAD` 핸드셰이크 → 응답 `0x8000BECD, 0x8000BEAD`
2. `0xBEDC 0`(해제) → 응답 `0x8000BEDC, 0`
3. `0xBEDC` 1~99(진행률, 응답 확인)와 WRITE_10 데이터 조각
4. `0xBEDC 100`(잠금, 응답 확인). 앞 단계가 실패해도 시도합니다.
5. `--reboot`를 줄 때만: `0xBEC1 0`(응답 없음)

READ_10은 하나도 보내지 않고, LBA 170 WRITE_10도 보내지 않습니다. `0xBEC2`·`0xBEC5`·`0xBEC6`·`0xBEC7`은 패킷을 만들기 전에 거부합니다. ini의 `Reset = 1`만으로는 재부팅하지 않습니다. 명령 수는 BOOT1만 쓸 때 18개(`0x88` 7, `0x89` 7, WRITE_10 4), BOOT1+LINUX.DAT일 때 702개(`0x88` 101, `0x89` 101, WRITE_10 500)입니다. `--reboot`를 주면 `0x88`이 하나 더 붙습니다.

#### 제어 경로를 바꾼 이유 (S6, 2026-11-17)

- **실물 결과(2026-11-17 21:55:20)**: 해제 READ_10(LBA 170, 30섹터)이 `host_status=7`로 끝났습니다. 같은 순간 usb-storage가 장치를 리셋했고, 데이터 WRITE_10은 하나도 나가지 않았습니다. 로그는 `30_TEST/40_Build/writer/exec-boot1.jsonl`에 있습니다.
- **원인(참조 V16 Boot0)**: READ_10은 읽기 전에 잠금 플래그 `[0x30d6f412]`를 `[sp]`에 저장하고(`0xdb50`~`0xdb58`), 읽은 뒤 값이 바뀌었으면 CSW 상태 7을 냅니다(`0xdc10`~`0xdc28`). LBA 170에서 30섹터를 읽으면 플래그가 1에서 0으로 바뀝니다(`0xc4ec`). 그래서 해제 READ는 매번 CSW 7로 끝나고, Linux에서는 쓸 수 없습니다.
- **대체 경로**: 잠금 플래그를 쓰는 곳은 세 군데뿐입니다. `0xc4d4`~`0xc4f4`(LBA 170 READ 30/31), `0xc8c4`~`0xc8ec`(패킷 `0xBEDC`: 인자 < 100이면 0, ≥ 100이면 1), `0xe668`(초기화 시 1). SCSI `0x88`은 `0xde70` → `0xedd4` → 콜백 `[0x30e8f9b4]` = `0x30c087dc`(파일 `0xc7dc` 패킷 파서)로 가며, 잠금 검사가 없습니다. 처리기는 끝에서 상태 바이트를 0으로 정리합니다(`0xde88`~`0xdea8`). 따라서 `0xBEDC 0`으로 해제하고 `0xBEDC 100`으로 잠급니다.
- WRITE_10 모드 2(`0xc578`)는 플래그가 0일 때만 기록합니다. 1이면 `0x27`을 기록하고 아무것도 쓰지 않습니다. dry-run 모형은 이런 WRITE를 `write-protected`로 표시합니다.
- 패킷 형식은 `20_HPPrime-Tool/hpprime/vendor.py`와 같습니다. CDB는 `88 00…`/`89 00…`(10 B)이고, 패킷은 `<II cmd,arg>` + 256 B입니다.

#### 실물 1차 시험: `probe`

NAND 쓰기도 잠금 변경도 없이 `0x88 0xBECD`와 `0x89` 응답 하나만 주고받습니다. `--acknowledge-nand-write`는 필요 없습니다.
```bash
python3 tools/usbtool_v3.py probe --device /dev/sdX --execute --confirm-g1 --confirm-recovery-v16
```
정상 출력은 `PROBE_ACK: 0x8000BECD 0x8000BEAD (expected 0x8000BECD 0x8000BEAD) OK`입니다. 실패하면 `PROBE_FAILED: …`와 종료 코드 2가 나옵니다. 기록은 `30_TEST/log/<시각>_usbtool_v3_probe.jsonl`에 남습니다. 파서 `0xc7dc`의 `0xBECD` 분기는 잠금 플래그에 쓰지 않으므로 잠금 상태가 바뀌지 않습니다.

실물 기록은 아래 플래그가 **모두** 있을 때만 장치를 엽니다. 여는 순서는 `tools/device_check.py` 검사를 통과한 다음 `O_RDWR|O_EXCL`입니다. 검사 항목은 `03f0:0441`, 전체 디스크, 512 B 섹터, 용량 268173312, 마운트·holder·swap 없음입니다.
```bash
python3 tools/usbtool_v3.py flash --ini usbtool.ini --execute --device /dev/sdX \
    --confirm-g1 --confirm-recovery-v16 --acknowledge-nand-write \
    --expected-sha256 <BOOT1.ROM sha256> --expected-sha256 <LINUX.DAT sha256>
```
sha256 값은 dry-run 출력의 `PLAN` 줄에 나옵니다. 보낸 SCSI 명령은 모두 `30_TEST/log/<시각>_usbtool_v3_flash.jsonl`에 남습니다. dry-run에서는 `--log-jsonl [PATH]`로 켭니다. 기록 뒤에 다시 읽어 비교하지 않습니다. 복구 모드 READ_10은 합성 응답이기 때문입니다. 확인은 5단계 Linux `nanddump`로 합니다.

