# 20_HPPrime-Tool — HP Prime G1 통합 도구

`30_TEST/20_ALL-Backup`(읽기 전용 백업)과 `50_Recorvery`(Boot0 업데이트)를 하나로 합친 도구다(W6, 2026-11-17).
**이 도구는 기록 경로를 코드로 막는다.** 실물에 쓰는 명령은 없다. 실물을 읽는 명령은 `dump --execute` 하나뿐이다.

## 1. 무너진 전제 (반드시 먼저 읽는다)

1. **일반 복구 USB 디스크는 NAND가 아니다.** V16 Boot0의 mode 2 READ_10은 NAND를 읽지 않는다. 버퍼를 0으로 채우고 LBA 0, `0x20000`, `0x20008`, `0x20018` 네 섹터만 합성한다. 그래서 옛 `prepatch` 백업(268,173,312바이트)은 NAND 백업이 아니라 **펌웨어 합성 응답**이다. `synth-check`로 확인할 수 있다. 근거는 `docs/Handoff-v2.md` 4-1절에 있다.
2. **"물리 NAND = USB 논리 + 0x40000" 공식은 근거가 없다.** 0x40000은 펌웨어 버퍼 크기였다. 이 도구는 이 공식으로 계산하지 않는다. 기록 계획 출력에는 `"physical_offset": "UNVERIFIED"`로만 표시한다.
3. **"Boot0 업데이트는 검증된 경로다"라는 전제도 없다.** 주소 매핑이 검증되지 않았다. 원본 Boot0도 확보하지 못했다. 그래서 Boot0 패치는 보류하며, 실물 Boot0 경로는 코드에서 거부된다. 원본 Boot0는 나중에 Linux 경로로 확보할 계획이다.

`docs/`의 두 인수인계 문서는 원문 그대로 옮겼다. 두 문서에 남은 `+0x40000` 매핑 서술은 당시 기록이므로 위 1~3절이 우선한다.

## 2. 안전 규칙

- 계산기, USB, 블록 장치를 건드리는 명령은 사용자가 직접 실행한다. 프로그램은 sudo, 마운트 해제, USB reset, 드라이버 분리, 권한 변경을 하지 않는다.
- `status`, `check`, `verify`, `synth-check`, `plan`, `boot0`(모의)는 장치를 열지 않는다. `check`는 sysfs/procfs 텍스트만 읽는다.
- `dump`는 `--execute`가 없으면 사전 검사만 한다. 장치를 열지 않고 출력 폴더도 만들지 않는다.
- 장치 경로는 자리표시자(`/dev/sdX`)로 쓰지 않는다. `status`로 확인한 실제 전체 디스크만 쓴다.
- 실행할 때마다 `30_TEST/log/<UTC>_HPPrime-Tool_XXXX.log`에 명령, 출력, 종료 코드가 남는다.

### 코드로 막은 것 (차단 목록)

| # | 차단 | 위치 | 시험 |
| --- | --- | --- | --- |
| a | `boot0 --execute`는 모든 확인 옵션을 줘도 거부된다: `blocked: NAND address mapping unverified; see docs`. `LinuxTransport` 생성도 같은 이유로 거부되고, 장치를 열지 않는다. 모의 실행은 유지한다 | `cli.run_boot0`, `vendor.LinuxTransport.__init__` | `tests/test_safety.py` Boot0BlockTests |
| b | `plan`과 `boot0` 요약의 `physical_offset`은 항상 `"UNVERIFIED"`다. `+0x40000` 계산 코드는 없다(소스 검사 시험 포함) | `vendor.UpdateImage.summary` | PhysicalOffsetTests |
| c | 시작 LBA 170 읽기와 30·31섹터 읽기는 모든 프로필에서 ioctl 전에 거부된다. 512바이트 정렬과 16 KiB 이하 제한, 영역 밖 거부도 유지한다 | `dump.check_read_request` | ControlRequestTests, TransportTests |
| d | 계획한 NAND 기록은 상한 `0x3EC0000`을 넘을 수 없다 | `vendor.WRITE_LIMIT`, `load_image` | WriteLimitTests, PlanTests |
| e | 옛 프로필 이름 `hpprime-g1-recovery-prepatch`는 `verify`만 받아들인다. 새 덤프는 이 이름으로 만들 수 없다 | `dump.PROFILE_ALIASES` | LegacyProfileTests |
| - | 쓰기용 SCSI 경로가 없다. 덤프는 `O_RDONLY`(장치는 `O_EXCL` 추가)와 READ_10만 쓴다. 벤더 전송은 264바이트 `0x88`/`0x89` 형식만 허용한다 | `dump.ReadOnlySource`, `vendor.LinuxTransport.transfer` | TransportTests, VendorTests |
| - | `dump --source disk-response`는 디스크와 모든 파티션의 sysfs `ro=1`이 확인되지 않으면 장치를 열지 않는다 | `device.require_kernel_read_only` | PreflightTests |

### 2.1 추가 차단 (2026-11-17)

- vendor `0xBEC2`는 패킷을 만들지 않고 `PermissionError`로 거부한다. 참조 V16에서 이 명령은 Boot0 블록 0을 지우고 다시 쓴다(`0x7988`).
- 주소 변환은 `S1_write10-mapping.md`로 정적 분석까지 확정됐다. 그래도 실물 표 첫 값을 확인하기 전까지 `physical_offset`는 `"UNVERIFIED"`로 둔다.

## 3. 구조

```text
20_HPPrime-Tool/
├── cli.py                 # 하위 명령: status, check, dump, verify, synth-check, plan, boot0
├── hpprime/__init__.py    # 경로 상수, BLOCKED_REASON
├── hpprime/transport.py   # SgIoHdr, SG_IO 상수, READ_10 CDB, sysfs 탐지 (usbtool_v3에서 복사, 의존 없음)
├── hpprime/device.py      # 통합 inspect_device (허용 용량·HP VID/PID·MSC·마운트/holder/swap 검사), ro 검사
├── hpprime/log.py         # Tee + execution_log(접두어 인자, 로그 폴더 30_TEST/log)
├── hpprime/dump.py        # 읽기 전용 덤프·매니페스트·검증·프로필
├── hpprime/vendor.py      # 0xBECx 패킷, INI 계획, 모의 전송, 차단된 실물 전송
├── hpprime/synth.py       # 합성 섹터 대조 (옛 check_synth_sectors.py)
├── host/                  # 셸·bpftrace·udev 파일 (원문 복사, 경로 변수 2곳만 수정)
├── tests/                 # 옛 시험 66개 + 새 시험 29개
└── docs/                  # Handoff-v3.md (재개 지점), 옛 README 2개(원문 그대로)
```

## 4. 사용법

작업공간 루트(`$WORKSPACE`)에서 실행한다.

```bash
/bin/python -B 30_TEST/20_HPPrime-Tool/cli.py status
/bin/python -B 30_TEST/20_HPPrime-Tool/cli.py verify 10_Backup/20261116/g1-prepatch_20261116T060805_cbd06ce1
/bin/python -B 30_TEST/20_HPPrime-Tool/cli.py synth-check 20_Repo/G1/HPPrimeG1FileDumper/BXCBOOT0V16.bin /path/to/recovery-disk.bin
/bin/python -B 30_TEST/20_HPPrime-Tool/cli.py plan --ini 20_Repo/G1/HPPrimeG1FileDumper/usbtool.ini
/bin/python -B 30_TEST/20_HPPrime-Tool/cli.py plan --ini 20_Repo/G1/HPPrimeG1FileDumper/usbtool.ini --bad-blocks 3
/bin/python -B 30_TEST/20_HPPrime-Tool/cli.py boot0 --variant v16
```

`plan`은 `hpprime/layout.py`로 이미지마다 LBA, 논리·물리 블록, 겹치는 MTD 파티션, WRITE_10 조각 수(블록당 1조각, 256섹터)를 함께 낸다. 변환은 참조 V16 정적 분석 모델(`table[LBA>>8]`, 불량 블록은 `--bad-blocks N,N,...`로 가정)이며 결과에 `"mapping": "model: reference V16 static analysis; real table unverified"`를 붙인다. `physical_offset`은 그대로 `UNVERIFIED`다. 다음 경우 오류로 거부한다: (a) 물리 블록 0·1에 닿음, (b) LBA ≥ 0x7FE00 또는 끝 > 0x3EC0000, (c) 시작 오프셋이 128 KiB 블록 경계가 아님, (d) 이미지끼리 같은 블록을 씀, (e) Boot0 이미지. 실물 기록 기능은 없다.

시험은 아래 명령으로 돌린다. 장치 없이 오프라인으로만 실행한다.

```bash
cd 30_TEST/20_HPPrime-Tool && /bin/python -B -m unittest discover -s tests -v 2>&1 | tail -15
```

- `check --device DEV --profile P`: 프로필은 필수다. 용량으로 프로필을 자동 선택하지 않는다(옛 도구 원칙, 2026-11-17 오케스트레이터 수정).
- `dump --source filedumper|disk-response|image`: 각각 FileDumper 패치 장치의 NAND/OOB, 일반 복구 디스크 응답(NAND 아님), 저장된 528 MiB 가상 디스크 이미지를 덤프한다.
- `synth-check`: 종료 코드 0은 모두 일치, 3은 불일치, 2는 V16 Boot0가 아닌 이미지다.

## 5. 옛 명령 → 새 명령 대응표

| 옛 명령 | 새 명령 |
| --- | --- |
| `20_ALL-Backup/backup.py status` | `cli.py status` |
| `backup.py check --device D --profile hpprime-g1-recovery-prepatch` | `cli.py check --device D --profile hpprime-g1-recovery-disk-response` (옛 이름도 허용, 프로필 필수) |
| `backup.py backup --device D --confirm-g1 --confirm-filedumper [--execute]` | `cli.py dump --source filedumper --device D --confirm-g1 --confirm-filedumper [--execute]` |
| `backup.py prepatch --device D --confirm-g1 --confirm-recovery-v16 [--execute]` | `cli.py dump --source disk-response --device D --confirm-g1 --confirm-recovery-v16 [--execute]` |
| `backup.py extract --image I [--execute]` | `cli.py dump --source image --image I [--execute]` |
| `backup.py verify DIR` | `cli.py verify DIR` |
| `check_synth_sectors.py BOOT0 DISK` | `cli.py synth-check BOOT0 DISK` |
| `50_Recorvery/usbtool.py status` / `check --device D` | `cli.py status` / `cli.py check --device D` |
| `usbtool.py plan --ini F` | `cli.py plan --ini F [--bad-blocks N,N,...]` (`physical_offset` = `UNVERIFIED`, layout 모델 결과 포함) |
| `usbtool.py boot0 --variant V` | `cli.py boot0 --variant V` (모의) |
| `usbtool.py boot0 ... --execute` | **차단됨** (`blocked: NAND address mapping unverified; see docs`) |
| `test_backup.py -v`, `test_usbtool.py -v` | `python -B -m unittest discover -s tests -v` |
| `20_ALL-Backup/*.sh`, `*.bt`, `*.rules` | `host/` 아래 같은 이름 |

이름이 바뀐 것: 프로필 `hpprime-g1-recovery-prepatch`는 `hpprime-g1-recovery-disk-response`로, 출력 폴더 접두어 `g1-prepatch_`는 `g1-disk-response_`로, 로그 접두어 `ALL-Backup_`/`Recovery_`는 `HPPrime-Tool_`로 바뀌었다. 이미 있는 백업 폴더와 매니페스트는 그대로 `verify`할 수 있다.

## 6. host/ 파일

원문 그대로 Read→Write로 옮겼다. SHA-256 대조 결과는 `30_TEST/30_Boot0-Path-Survey/work/W6/copy-check.txt`에 있다. 다만 `host-ata-guard.sh`와 `trace-reset.sh`는 한 단계 더 깊어진 위치 때문에 `output_root`를 `../../10_Backup` → `../../../10_Backup`으로 고쳤다. diff는 같은 폴더의 `copy-path-fix.diff`에 있다. 모두 sudo가 필요한 호스트 설정 도구이며, 사용자가 검토한 뒤 직접 실행한다. `self-test`는 장치 없이 돌아간다.

## 7. 남은 한계

- `docs/`의 상대 링크(`../../10_Backup`, `backup.py` 등)는 옛 폴더 기준이라 새 위치에서는 맞지 않는다. 내용 수정 금지 원칙에 따라 고치지 않았다.
- `vendor.py`의 참조 Boot0 경로는 `20_Repo/G1/HPPrimeG1FileDumper`다. Boot0 시험은 이 파일들이 있어야 통과한다.
