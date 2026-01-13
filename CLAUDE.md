# Sensor Gap Imputer 프로젝트

## 프로젝트 개요

CSV 형식의 센서 데이터에서 시간 누락을 감지하고, 누락된 데이터를 보간하여 Oracle 11g용 INSERT SQL문을 생성하는 도구입니다.

## 핵심 기능

**CSV → 시간 누락 감지 → 보간 → INSERT SQL 생성**

### 주요 처리 단계

1. **입력 데이터 검증**

   - CSV 파일 읽기 및 파싱
   - 필수 컬럼 존재 여부 확인
   - 기본 데이터 무결성 검증

2. **시간 누락 감지**

   - 시계열 데이터의 시간 간격 분석
   - 누락된 시간대 식별

3. **데이터 보간**

   - 누락된 시간대의 센서 값 추정
   - 이전/이후 데이터 기반 선형 보간
   - 변동률 검증 (±1% 이내)

4. **INSERT SQL 생성**
   - Oracle 11g 호환 INSERT ALL 구문 생성
   - 최대 10줄 단위 배치 처리
   - 타임스탬프 형식 준수

## 기술 스택

- **언어**: Python 3.12
- **의존성 관리**: Poetry
- **주요 라이브러리**: pandas, random (내장)

## 프로젝트 상수

```python
INPUT_CSV_PATH = Path("storage/input/COM_SENSOR_DATA.csv")
OUTPUT_DIR = Path("storage/output")
REQUIRED_CONSTANT_COLUMNS = ["EQUIP_SN", "FARM_ID", "SITE_ID", "MEAS_DEPTH"]
SENSOR_COLUMNS = ["VAL_TP", "VAL_DO", "VAL_DS", "VAL_PH", "VAL_OR", "VAL_SL"]
TIME_INTERVAL = timedelta(hours=1)
MAX_VARIATION_RATES = {
    "VAL_TP": 0.015,  # 수온: ±1.5%
    "VAL_DO": 0.005,  # 용존산소: ±0.5%
    "VAL_DS": 0.005,  # 포화도: ±0.5%
    "VAL_PH": 0.005,  # pH: ±0.5%
    "VAL_OR": 0.01,   # ORP: ±1%
    "VAL_SL": 0.005,  # 염도: ±0.5%
}
DECIMAL_PLACES = 2  # 소수점 자릿수
BATCH_SIZE = 10  # INSERT ALL 최대 줄 수
```

## 디렉토리 구조

```
sensor-gap-imputer/
├── storage/
│   ├── input/          # 입력 CSV 파일
│   │   └── COM_SENSOR_DATA.csv
│   └── output/         # 생성된 SQL 파일
├── main.py             # 전체 로직 (단일 파일)
├── pyproject.toml      # Poetry 설정
├── CLAUDE.md           # 프로젝트 문서 (현재 파일)
└── TODO.md             # 작업 목록
```

## 데이터 처리 규칙

### 입력 데이터 제약사항

**필수 동일 컬럼** (다를 경우 오류 발생 및 중지):

- `EQUIP_SN`: 장비 시리얼 번호
- `FARM_ID`: 양식장 ID
- `SITE_ID`: 장소 ID
- `MEAS_DEPTH`: 측정 수심

위 컬럼들은 CSV 전체에서 동일한 값을 가져야 하며, INSERT문에 그대로 사용됩니다.

### 보간 대상 센서 값

다음 6개 센서 값을 보간합니다 (장비에 따라 2~6개):

1. `VAL_TP`: 수온
2. `VAL_DO`: 용존산소
3. `VAL_DS`: 포화도
4. `VAL_PH`: pH
5. `VAL_OR`: ORP
6. `VAL_SL`: 염도

**보간 판단 기준**:

- 각 컬럼에 1개 이상의 데이터가 존재하면 보간 수행
- 1개도 없으면 NULL 처리

**소수점 처리**:

- 모든 센서 값은 소수점 2자리로 라운딩
- `DECIMAL_PLACES = 2` 상수로 관리

### 보간 알고리즘

#### 센서별 변동률 설정

각 센서는 고유한 변동률을 가지며, `MAX_VARIATION_RATES` 상수로 관리합니다:

```python
MAX_VARIATION_RATES = {
    "VAL_TP": 0.015,  # 수온: ±1.5%
    "VAL_DO": 0.005,  # 용존산소: ±0.5%
    "VAL_DS": 0.005,  # 포화도: ±0.5%
    "VAL_PH": 0.005,  # pH: ±0.5%
    "VAL_OR": 0.01,   # ORP: ±1%
    "VAL_SL": 0.005,  # 염도: ±0.5%
}
```

**변동률 의미**:
- 각 시간 단계마다 센서값이 변할 수 있는 최대 비율
- 예: `VAL_TP = 0.015`는 수온이 매 시간 현재값의 ±1.5% 이내로 변화 가능함을 의미

#### 기본 원칙

1. 누락 구간의 이전/이후 데이터를 파악
2. 센서별 변동률로 변해도 목표값 도달 가능한지 검증
3. 변동률 이내 랜덤 변화로 보간하되 목표값 도달 보장

#### 검증 로직

**예시 1: 보간 가능 (수온, 변동률 1.5%)**
```
- 10시: 20도
- 11~13시: 누락 (3시간)
- 14시: 20.6도
- 필요 변화: 0.6도
- 최대 가능 변화: 20도 × 1.5% × 3시간 = 0.9도
- 0.6도 ≤ 0.9도 → OK
```

**예시 2: 보간 불가능 (용존산소, 변동률 0.5%)**
```
- 10시: 10.0 mg/L
- 11~13시: 누락 (3시간)
- 14시: 10.3 mg/L
- 필요 변화: 0.3 mg/L
- 최대 가능 변화: 10.0 × 0.5% × 3시간 = 0.15 mg/L
- 0.3 > 0.15 → 오류!
```

#### 랜덤 보간 방식

**증가 방향 (prev_value < next_value)**:

1. **중간 단계**:
   - 최소 변화량: `(필요 변화량 - 남은단계 × 최대변화량)`와 0 중 큰 값
   - 최대 변화량: `min(센서별 최대변화량, 필요 변화량)`
   - 범위 내에서 랜덤 선택

2. **마지막 단계**:
   - 목표값 인근에 도달하도록 랜덤 조정
   - 변동률 이내에서 목표값에 최대한 가깝게

**감소 방향 (prev_value > next_value)**:

1. **중간 단계**:
   - 절댓값으로 계산: `|필요 변화량| - 최대변화량 × (남은단계 - 1)`
   - 음수로 변환하여 적용
   - 최대 변화량 제한 준수

2. **마지막 단계**:
   - 증가 방향과 동일한 논리로 음수 범위에서 처리

**목표값 도달 보장**:
- 각 단계마다 "남은 시간 동안 최대 변화량으로 변해도 목표 도달 가능" 조건 유지
- 마지막 단계에서 목표값 인근 랜덤 도달
- 과도한 변화 방지 및 자연스러운 데이터 생성

#### 오류 조건

- 누락 시간 동안 센서별 변동률로 변해도 목표값에 도달할 수 없는 경우
- 오류 메시지에 센서명, 변동률, 필요/최대 변화량 포함
- 스크립트 실행 중지

### INSERT SQL 생성 규칙

#### 고정값 처리

- `QC_*` 컬럼: 모두 "O" 처리
- `REGI_TIME`: `ACQU_TIME`과 동일값 사용

#### INSERT SQL 포함 컬럼

다음 20개 컬럼만 SQL문에 명시:

```
EQUIP_SN, ACQU_TIME, MEAS_LAYER, FARM_ID, SITE_ID, MEAS_DEPTH,
VAL_TP, VAL_DO, VAL_DS, VAL_PH, VAL_OR, VAL_SL,
QC_TP, QC_DO, QC_DS, QC_PH, QC_OR, QC_SL,
REGI_TIME, DATA_SOURCE_TYPE
```

#### 기타 컬럼

위 컬럼을 제외한 나머지는 SQL문에서 명시하지 않음 (DDL의 DEFAULT값 자동 적용)

#### SQL 포맷

- **Oracle 11g** `INSERT ALL ... SELECT 1 FROM DUAL` 구문
- **최대 10줄** 단위로 배치 처리
- 타임스탬프 형식: `TIMESTAMP 'YYYY-MM-DD HH24:MI:SS.000000'`

**예시**:

```sql
INSERT ALL
    INTO COM_SENSOR_DATA (EQUIP_SN,ACQU_TIME,MEAS_LAYER,...) VALUES ('MSB-M-250006',TIMESTAMP '2026-01-03 20:00:00.000000',3,...)
    INTO COM_SENSOR_DATA (EQUIP_SN,ACQU_TIME,MEAS_LAYER,...) VALUES ('MSB-M-250006',TIMESTAMP '2026-01-03 21:00:00.000000',3,...)
    ...
SELECT 1 FROM DUAL;
```

## 타겟 테이블 정보

- **테이블명**: `GLOBIT.COM_SENSOR_DATA`
- **데이터베이스**: Oracle 11g
- **PRIMARY KEY**: `(EQUIP_SN, ACQU_TIME, MEAS_LAYER)`

상세한 DDL은 프로젝트 요구사항 문서 참조.

## 실행 방법

```bash
# 의존성 설치
poetry install

# 스크립트 실행
poetry run python main.py
```

### 실행 결과

스크립트 실행 시 다음과 같은 출력을 확인할 수 있습니다:

```
=== 센서 데이터 보간 및 SQL 생성 시작 ===

1. CSV 파일 읽기: storage/input/COM_SENSOR_DATA.csv
   총 N개 레코드 읽음

2. 필수 컬럼 검증
   EQUIP_SN: MSB-M-250006
   FARM_ID: ...
   SITE_ID: ...
   MEAS_DEPTH: ...

3. 시간 누락 감지
   누락된 시간대: N시간
   - YYYY-MM-DD HH:MM:SS ~ YYYY-MM-DD HH:MM:SS (N시간)

4. 보간 대상 센서 컬럼 판단
   보간 대상: VAL_TP, VAL_DO, ...

5. 데이터 보간 수행
   [DEBUG] random_interpolate_with_guarantee 호출: ...
   보간 완료 (총 N개 레코드)

6. INSERT SQL 생성
SQL 파일 생성 완료: storage/output/insert_estimated_data.sql
보간된 데이터 개수: N개

=== 처리 완료 ===
```

### 출력 파일

- **위치**: `storage/output/insert_estimated_data.sql`
- **내용**: 보간된 데이터만 포함된 INSERT SQL
- **특징**: DATA_SOURCE_TYPE = 'ESTIMATED'인 행만 출력

## 주의사항

1. **데이터 무결성**: EQUIP_SN, FARM_ID, SITE_ID, MEAS_DEPTH는 CSV 전체에서 동일해야 함
2. **센서별 변동률**: 각 센서마다 다른 변동률 적용 (수온 ±1.5%, 기타 ±0.5%~1%)
3. **보간 범위**: 센서별 변동률 초과 시 오류 발생 및 중지
4. **SQL 안정성**: INSERT ALL 구문은 최대 10줄로 제한
5. **타임스탬프**: Oracle TIMESTAMP 형식 준수 필수
6. **디버그 출력**: 보간 과정의 상세 로그가 콘솔에 출력됨

## 코딩 표준

### 필수 규칙

**예외 처리**:

- 예외를 숨기고 None 반환하는 패턴 금지
- 명확한 에러 핸들링 필요

**타입 안정성**:

- 모든 함수에 타입 힌트 필수
- Optional 타입은 `|` 문법 사용 (예: `str | None`, `int | None`)
- 여러 타입 허용 시에도 `|` 사용 (예: `int | float`, `Path | str`)

**파일 처리**:

- Path 객체만 사용 (문자열 경로 금지)

**문서화**:

- Google 스타일 Docstring
- 한글 작성
- 복잡한 로직은 넘버링 주석
- **주석 작성 원칙**:
  - 현재 코드의 상태와 동작만 설명
  - 과거 상태, 변경 이력, 계획 단계는 기록하지 않음
  - 금지 패턴: "Phase 0", "Phase 3", "레드", "그린" 등 개발 단계 표현 사용 금지

**네이밍**:

- 함수/변수: `snake_case`
- 클래스: `PascalCase`
- 상수: `UPPER_SNAKE_CASE`

### 개발 도구

- **간결성 우선**: 코드 품질 도구(Black, Ruff, PyRight) 및 자동화 테스트(pytest) 미사용
- **실전 검증**: 실제 CSV 파일로 직접 테스트

## 개발 원칙

1. **YAGNI**: 필요성이 확인될 때 구현
2. **간결성**: 불필요한 추상화 지양
3. **확장성**: 도메인별 모듈 독립성 유지
4. **사용자 중심**: 한글 메시지, 명확한 오류 정보

## 주요 함수 구조

### 핵심 함수

1. **`load_csv()`**: CSV 파일 읽기 및 시간 정렬
2. **`validate_constant_columns()`**: 필수 동일 컬럼 검증
3. **`identify_missing_timestamps()`**: 시간 누락 감지
4. **`check_sensor_columns()`**: 보간 대상 센서 판단
5. **`validate_variation_rate()`**: 센서별 변동률 검증
6. **`random_interpolate_with_guarantee()`**: 랜덤 보간 (목표값 도달 보장)
7. **`interpolate_missing_data()`**: 전체 보간 프로세스 조율
8. **`generate_insert_sql()`**: INSERT SQL 생성

### 보간 프로세스 흐름

```
1. 누락 시간대를 연속된 그룹으로 분리
   └─→ 각 그룹별로 독립적으로 처리

2. 각 그룹마다:
   ├─ 이전/이후 데이터 찾기
   ├─ 센서별 변동률 검증 (validate_variation_rate)
   └─ 시간 단계별 랜덤 보간 (random_interpolate_with_guarantee)

3. 보간된 행에 DATA_SOURCE_TYPE = "ESTIMATED" 마킹

4. 원본 데이터와 병합 후 시간순 정렬
```

## 참고 자료

- **Oracle 11g INSERT ALL 구문**: [Oracle Documentation](https://docs.oracle.com/cd/E11882_01/server.112/e41084/statements_9014.htm)
- **보간 알고리즘**: 랜덤 보간 + 센서별 변동률 제약 + 목표값 도달 보장
- **변동률 설정 원리**: 각 센서의 물리적 특성에 따라 다른 변동률 적용
