# HP Prime G1 백업

Linux에서 일반 복구 디스크 또는 FileDumper의 NAND/OOB를 읽어 보관하는 프로그램이다.
기본 저장 위치는 [10_Backup](../../10_Backup), 실행 로그는 [30_TEST/log](../log)다.
Python 3.11 이상과 표준 라이브러리를 사용하며 별도 패키지 설치는 필요 없다.

## 패치 전 일반 복구 디스크 백업

G1 Revision C, 사용자 확인 복구 화면 `v16`, 268,173,312바이트(255.75 MiB)의 일반 복구 디스크를 위한 별도 `prepatch` 명령이다.
**USB에 노출된 논리 디스크만 보존한다. 원본 Boot0, OOB, 물리 배드블록 배치는 포함하지 않으며 전체 NAND 백업이 아니다.**
주소 변환 및 원본 Boot0 복원을 입증하지 않는다. `recovery-disk.bin`을 물리 NAND에 그대로 쓰지 않는다.

복구 모드 진입은 [복구 도구 문서](../../50_Recorvery/README.md)를 따른다.
워크스페이스 루트에서 아래 명령을 사용한다. `/dev/sdX`는 재연결 후 확인한 실제 장치로 바꾼다.

```bash
/bin/python -B 30_TEST/20_ALL-Backup/backup.py check --device /dev/sdX --profile hpprime-g1-recovery-prepatch
/bin/python -B 30_TEST/20_ALL-Backup/backup.py prepatch --device /dev/sdX --confirm-g1 --confirm-recovery-v16 --output-root 10_Backup/20261116
```

두 명령은 사전 검사만 한다. 실제 저장을 하려면 두 번째 명령에 `--execute`를 추가한다.
이미 있는 날짜 폴더를 덮어쓰지 않고 그 안에 `g1-prepatch_UTC시각_ID` 하위 폴더를 만든다.

- `recovery-disk.bin`: USB 오프셋 0부터 268,173,312바이트.
- `manifest.json`: 전용 프로필 `hpprime-g1-recovery-prepatch`, 범위·출처·시각·SHA-256 기록.
- 전체를 16 KiB 단위로 두 번 읽고 저장 파일도 해시 검사한다. 장치 읽기 총량은 511.5 MiB다.
- 시작 LBA 170인 요청과 30·31섹터 요청은 전송 전에 거부한다. 실제 전체 읽기 계획은 모두 32섹터다.
- `--execute`가 없으면 장치를 열지 않는다. `status`의 블록 이름만 믿지 말고 `check` 성공을 확인한다.
- 용량으로 프로필을 자동 선택하지 않는다. FileDumper 프로필과 일반 복구 프로필의 용량을 교차 적용하면 거부한다.

아래 `verify` 명령은 매니페스트의 프로필에 맞춰 두 종류의 백업을 모두 검증한다.
[2026-11-16 작업 기록](../../10_Backup/20261116/README.md)에 실물 백업 여부와 복구 후보 자료를 구분해 기록했다.

## 전체 NAND/OOB 지원 조건

- **이미 FileDumper 매핑이 적용된 G1 장치만 지원한다.** 일반 HID 모드와 순정 복구 모드에서는 전체 백업을 수행하지 않는다.
- 전체 NAND/OOB 명령은 [참조 덤프 절차](../../20_Repo/G1/HPPrimeG1FileDumper/README.md)의 528 MiB 가상 디스크를 기준으로 한다.
- USB ID와 용량만으로 세대나 펌웨어를 확정할 수 없다. 사용자가 G1 실물과 적용된 FileDumper 매핑을 별도로 확인해야 한다.
- Boot0 패치, 펌웨어 업데이트, 쓰기 잠금 변경, 초기화, 재부팅, 복구 모드 전환, 마운트 해제, 권한 변경은 하지 않는다.
- **패치 이전 원본 Boot0는 이 프로그램으로 되살릴 수 없다.** 백업에는 읽는 시점의 Boot0가 포함된다.
- NAND의 사용자 데이터·펌웨어·배드블록 위치와 OOB를 보존한다. RAM, CPU 레지스터, 실행 중 상태까지 저장하는 스냅샷은 아니다.
- SHA-256 검증은 읽기 일관성과 저장 무결성 검사다. 펌웨어 매핑의 정확성, ECC 복구 가능성, 실물 복원 성공을 증명하지 않는다.
- [기존 V3 도구](../Linux-For-HPPrime-V3/tools/usbtool_v3.py)의 SG_IO 구조체·CDB 생성 함수·sysfs 탐색 함수만 공유한다. 기존 쓰기 전송부와 플래싱 컨트롤러는 사용하지 않는다. 두 폴더의 상대 위치를 유지한다.

## 주소와 결과

| 결과 파일 | USB 가상 디스크 시작 | 크기 | 의미 |
| --- | --- | --- | --- |
| nanddump.bin | 256 MiB | 256 MiB | 물리 NAND 시작부터 전체 |
| oobdump.bin | 512 MiB | 8 MiB | 전체 NAND 페이지의 OOB |

주소는 끝 제외 기준으로 NAND `[256, 512) MiB`, OOB `[512, 520) MiB`다.
V3 플래싱의 `+0x40000` 매핑을 여기에 적용하지 않는다.
가상 디스크의 `[0, 256) MiB`와 나머지 구간은 읽지 않는다.

매 실행마다 고유한 `g1-nand-oob_UTC시각_ID` 폴더를 만든다. 이미 있는 폴더는 덮어쓰지 않는다.
폴더 권한은 0700, 로그 권한은 0600이다. 덤프에는 개인 데이터가 포함되므로 공개 저장소에 올리지 않는다.

- `nanddump.bin`: 268,435,456 바이트.
- `oobdump.bin`: 8,388,608 바이트.
- `manifest.json`: 출처, 주소, 크기, SHA-256, 두 번째 읽기 해시, 시각, 완료 여부.
- 실패 시 `.partial` 파일과 실패 매니페스트를 보존한다. `status`가 `complete`가 아니면 전체 백업으로 사용하지 않는다.

두 영역을 각각 두 번 읽고 저장 파일도 다시 해시 검사한다. 장치에서 읽는 총량은 528 MiB,
저장 데이터는 264 MiB이며 최소 265 MiB의 여유 공간을 요구한다.
진행 출력과 오류, UTC 시작·종료 시각, 종료 코드는 자동으로 실행 로그에 남긴다.

## 실행

아래 명령은 워크스페이스 루트에서 실행한다. `/dev/sdX`는 예시이며 반드시 실제 장치로 바꾼다.
장치를 임의로 자동 선택하지 않는다. 다중 연결 시에도 명시한 대상만 검사한다.

1. 현재 연결 상태 확인. USB 명령을 보내지 않고 sysfs만 읽는다.

   ```bash
   /bin/python -B 30_TEST/20_ALL-Backup/backup.py status
   ```

2. 이미 준비된 장치의 사전 검사. 아직 장치를 열거나 덤프하지 않는다.

   ```bash
   /bin/python -B 30_TEST/20_ALL-Backup/backup.py check --device /dev/sdX
   ```

3. G1과 FileDumper 매핑을 실제로 확인한 후에만 백업 실행.

   ```bash
   /bin/python -B 30_TEST/20_ALL-Backup/backup.py backup --device /dev/sdX --confirm-g1 --confirm-filedumper --execute
   ```

`--execute`를 빼면 사전 검사와 계획 출력만 한다. `--output-root`로 보관 루트를 바꿀 수 있다.
장치나 그 파티션이 마운트·스왑·다른 블록 장치에 사용 중이면 거부한다.
장치는 `O_RDONLY | O_EXCL`로 열며, 지정된 덤프 영역의 READ_10만 전송한다.
LBA 170 제어 명령, WRITE_10, 패치/해제 명령을 제공하지 않는다.
SG_IO 요청별 제한 시간은 10초이며 오류·짧은 응답은 즉시 실패 처리한다.
권한 부족 시 자동 sudo 또는 udev 변경을 하지 않는다. 임의로 시스템 디스크를 지정하지 않는다.

### 저장 파일 검증

출력된 백업 폴더를 지정해 파일 무결성을 재검사한다.

```bash
/bin/python -B 30_TEST/20_ALL-Backup/backup.py verify 10_Backup/g1-nand-oob_실제출력폴더
```

성공/사전 검사 정상 종료는 0, 처리 실패는 1, 인자 오류는 2, 사용자 중단은 130이다.
완료 판정에는 종료 코드뿐 아니라 매니페스트의 `status: complete`와 `verify` 통과가 필요하다.
강제 종료·전원 상실 시 종료 로그가 없거나 `in_progress`가 남을 수 있다.

## 오프라인 추출과 시험

이미 저장된 528 MiB 가상 디스크 이미지는 장치 연결 없이 처리할 수 있다.
일반 파일만 허용하며, 결과는 `image-extract_...` 폴더와 `source.kind: image-file`로 구분한다.
이 작업을 새 실물 백업으로 표시하지 않는다.

```bash
/bin/python -B 30_TEST/20_ALL-Backup/backup.py extract --image /path/to/virtual-disk.img --output-root /path/to/output --execute
/bin/python -B 30_TEST/20_ALL-Backup/test_backup.py -v
```

시험은 가짜 sysfs, 모의 SG_IO, 임시 528 MiB 및 255.75 MiB 합성 디스크를 사용하며 실물에는 접근하지 않는다.
실제 크기의 NAND/OOB 추출·바이트 전수 대조와 오류 경로를 검사한다.
임시 시험 이미지는 자동 삭제되고 시험 실행 로그는 남는다.
2026-11-16 기준 33개 테스트가 통과했다. 일반 복구 디스크 전수 해시 대조, 위험 읽기 차단, 용량 교차 거부, 마운트 거부, 사전 검사 무접근을 포함한다.
현재 실물의 전체 덤프와 복원은 검증되지 않았다. HID 상태에서 테스트 통과를 실물 백업 성공으로 해석하지 않는다.
