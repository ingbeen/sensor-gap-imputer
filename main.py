"""센서 데이터 시간 누락 감지 및 보간 후 INSERT SQL 생성 도구

CSV 파일의 시간 누락을 감지하고 선형 보간을 수행한 후
Oracle 11g용 INSERT SQL을 생성합니다.
"""

from pathlib import Path
from datetime import datetime, timedelta
from typing import Any
import pandas as pd
import random


# 상수 정의
INPUT_CSV_PATH = Path("storage/input/COM_SENSOR_DATA.csv")
OUTPUT_DIR = Path("storage/output")
REQUIRED_CONSTANT_COLUMNS = ["EQUIP_SN", "FARM_ID", "SITE_ID", "MEAS_DEPTH"]
SENSOR_COLUMNS = ["VAL_TP", "VAL_DO", "VAL_DS", "VAL_PH", "VAL_OR", "VAL_SL"]
TIME_INTERVAL = timedelta(hours=1)
# 센서별 변동률 (단위: 소수, 예: 0.01 = ±1%)
MAX_VARIATION_RATES = {
    "VAL_TP": 0.015,  # 수온
    "VAL_DO": 0.005,  # 용존산소
    "VAL_DS": 0.005,  # 포화도
    "VAL_PH": 0.005,  # pH
    "VAL_OR": 0.01,  # ORP
    "VAL_SL": 0.005,  # 염도
}
DECIMAL_PLACES = 2  # 소수점 자릿수
BATCH_SIZE = 10  # INSERT ALL 최대 줄 수


def load_csv(file_path: Path) -> pd.DataFrame:
    """CSV 파일을 읽어서 DataFrame으로 반환합니다.

    Args:
        file_path: CSV 파일 경로

    Returns:
        읽어온 DataFrame

    Raises:
        FileNotFoundError: 파일이 존재하지 않는 경우
        ValueError: CSV 파일 파싱 실패 시
    """
    if not file_path.exists():
        raise FileNotFoundError(f"CSV 파일을 찾을 수 없습니다: {file_path}")

    try:
        df = pd.read_csv(file_path)
        df["ACQU_TIME"] = pd.to_datetime(df["ACQU_TIME"])
        return df.sort_values("ACQU_TIME")
    except Exception as e:
        raise ValueError(f"CSV 파일 파싱 실패: {e}")


def validate_constant_columns(df: pd.DataFrame) -> dict[str, Any]:
    """필수 동일 컬럼들이 전체 CSV에서 동일한지 검증합니다.

    Args:
        df: 검증할 DataFrame

    Returns:
        검증된 상수 값들의 딕셔너리

    Raises:
        ValueError: 컬럼 값이 동일하지 않은 경우
    """
    constants = {}

    for col in REQUIRED_CONSTANT_COLUMNS:
        if col not in df.columns:
            raise ValueError(f"필수 컬럼 '{col}'이 CSV에 존재하지 않습니다")

        unique_values = df[col].unique()
        if len(unique_values) != 1:
            raise ValueError(
                f"컬럼 '{col}'의 값이 일치하지 않습니다. " f"발견된 값: {unique_values}"
            )

        constants[col] = unique_values[0]

    return constants


def identify_missing_timestamps(df: pd.DataFrame) -> list[datetime]:
    """시간 누락 구간을 식별합니다.

    Args:
        df: 시간 컬럼이 포함된 DataFrame

    Returns:
        누락된 시간대 리스트
    """
    timestamps = df["ACQU_TIME"].tolist()
    missing = []

    # 1. 전체 시간 범위 생성
    start_time = timestamps[0]
    end_time = timestamps[-1]

    # 2. 1시간 간격으로 모든 시간대 생성
    current = start_time
    expected_timestamps = []
    while current <= end_time:
        expected_timestamps.append(current)
        current += TIME_INTERVAL

    # 3. 누락된 시간대 찾기
    existing_set = set(timestamps)
    for expected in expected_timestamps:
        if expected not in existing_set:
            missing.append(expected)

    return missing


def format_missing_periods(
    missing_timestamps: list[datetime],
) -> list[tuple[datetime, datetime, int]]:
    """누락된 시간대를 연속된 기간으로 그룹화합니다.

    Args:
        missing_timestamps: 누락된 시간대 리스트

    Returns:
        (시작 시간, 종료 시간, 시간 개수) 튜플의 리스트
    """
    if not missing_timestamps:
        return []

    periods = []
    period_start = missing_timestamps[0]
    period_end = missing_timestamps[0]

    for i in range(1, len(missing_timestamps)):
        current = missing_timestamps[i]
        # 이전 시간과 연속되는지 확인 (1시간 간격)
        if (current - period_end).total_seconds() == 3600:
            period_end = current
        else:
            # 이전 기간 저장
            count = int((period_end - period_start).total_seconds() / 3600) + 1
            periods.append((period_start, period_end, count))
            # 새 기간 시작
            period_start = current
            period_end = current

    # 마지막 기간 저장
    count = int((period_end - period_start).total_seconds() / 3600) + 1
    periods.append((period_start, period_end, count))

    return periods


def check_sensor_columns(df: pd.DataFrame) -> list[str]:
    """보간 대상 센서 컬럼을 판단합니다.

    각 센서 컬럼에 1개 이상의 데이터가 존재하면 보간 대상으로 선정합니다.

    Args:
        df: 센서 데이터 DataFrame

    Returns:
        보간 대상 센서 컬럼 리스트
    """
    target_columns = []

    for col in SENSOR_COLUMNS:
        if col in df.columns:
            # NaN이 아닌 값이 1개 이상 존재하는지 확인
            if df[col].notna().sum() > 0:
                target_columns.append(col)

    return target_columns


def linear_interpolate(
    prev_time: datetime,
    prev_value: float,
    next_time: datetime,
    next_value: float,
    target_time: datetime,
) -> float:
    """선형 보간을 수행합니다.

    Args:
        prev_time: 이전 시간
        prev_value: 이전 값
        next_time: 다음 시간
        next_value: 다음 값
        target_time: 보간할 목표 시간

    Returns:
        보간된 값
    """
    # 1. 시간 간격 계산 (초 단위)
    total_seconds = (next_time - prev_time).total_seconds()
    target_seconds = (target_time - prev_time).total_seconds()

    # 2. 비율 계산
    ratio = target_seconds / total_seconds

    # 3. 선형 보간
    interpolated = prev_value + (next_value - prev_value) * ratio

    return interpolated


def validate_variation_rate(
    prev_value: float, next_value: float, missing_hours: int, sensor_column: str
) -> None:
    """누락 시간 동안 센서별 변동률로 변해도 목표값에 도달 가능한지 검증합니다.

    Args:
        prev_value: 시작 값
        next_value: 종료 값
        missing_hours: 누락된 시간 개수
        sensor_column: 센서 컬럼명 (변동률 확인용)

    Raises:
        ValueError: 설정된 변동률로 변해도 목표값 도달 불가능한 경우
    """
    if prev_value == 0:
        if abs(next_value) > 0:
            raise ValueError("시작 값이 0일 때 다음 값이 0이 아닙니다")
        return

    # 1. 센서별 변동률 가져오기
    variation_rate = MAX_VARIATION_RATES[sensor_column]
    variation_percent = variation_rate * 100

    # 2. 필요한 전체 변화량
    total_change_needed = abs(next_value - prev_value)

    # 3. 누락 시간 동안 변동률만큼 변할 수 있는 최대 변화량
    max_change_per_hour = abs(prev_value * variation_rate)
    max_total_change = max_change_per_hour * missing_hours

    # 4. 검증: 필요 변화량 <= 최대 변화량
    if total_change_needed > max_total_change:
        raise ValueError(
            f"보간 불가능 [{sensor_column}]: {missing_hours}시간 동안 {variation_percent}%씩 변화해도 목표값 도달 불가\n"
            f"필요 변화: {total_change_needed:.2f}, 최대 변화: {max_total_change:.2f}\n"
            f"(시작: {prev_value}, 종료: {next_value})"
        )


def random_interpolate_with_guarantee(
    prev_value: float,
    next_value: float,
    missing_hours: int,
    current_step: int,
    sensor_column: str,
) -> float:
    """센서별 변동률 이내 랜덤 변화로 보간하되 목표값 도달을 보장합니다.

    Args:
        prev_value: 현재 시점의 값
        next_value: 최종 목표값
        missing_hours: 전체 누락 시간 개수
        current_step: 현재 단계 (1부터 시작)
        sensor_column: 센서 컬럼명 (변동률 확인용)

    Returns:
        보간된 값
    """
    print(
        f"        [DEBUG] random_interpolate_with_guarantee 호출: {sensor_column}, 단계 {current_step}/{missing_hours}"
    )
    print(f"                현재값: {prev_value}, 목표값: {next_value}")

    # 1. 남은 변화량 계산
    remaining_change = next_value - prev_value
    print(f"                남은 변화량: {remaining_change:.2f}")

    # 2. 남은 단계 수
    remaining_steps = missing_hours - current_step + 1
    print(f"                남은 단계: {remaining_steps}")

    # 3. 센서별 변동률 이내에서 변화 가능한 범위
    variation_rate = MAX_VARIATION_RATES[sensor_column]
    max_change_this_step = abs(prev_value * variation_rate)
    print(
        f"                이번 단계 최대 변화량: ±{max_change_this_step:.2f} ({variation_rate*100}%)"
    )

    # 4. 이번 단계에서 최소/최대 변화량 계산
    if remaining_steps == 1:
        # 마지막 단계: 목표값 인근 랜덤 도달
        # 변동률 이내에서 목표값에 최대한 가깝게 도달
        if remaining_change > 0:
            # 증가 방향: 남은 변화량과 최대 변화량 중 작은 값 사용
            min_change = max(0, remaining_change - max_change_this_step * 0.5)
            max_change = min(max_change_this_step, remaining_change)
            print(
                f"                마지막 단계 (증가) → 변화량 범위: [{min_change:.2f}, {max_change:.2f}]"
            )
        else:
            # 감소 방향
            abs_remaining = abs(remaining_change)
            max_change = max(-max_change_this_step, remaining_change)
            min_change = min(0, remaining_change + max_change_this_step * 0.5)
            print(
                f"                마지막 단계 (감소) → 변화량 범위: [{min_change:.2f}, {max_change:.2f}]"
            )
    else:
        # 중간 단계: 남은 단계 동안 최대 변화량으로 변해서 목표 도달 가능한 범위 계산
        if remaining_change > 0:
            # 증가 방향
            # 최소: 이번에 적어도 (목표 - 남은단계×최대변화) 만큼 변해야 함
            min_change_this_step = remaining_change - (
                max_change_this_step * (remaining_steps - 1)
            )
            min_change = max(0, min_change_this_step)
            max_change = min(max_change_this_step, remaining_change)
            print(
                f"                증가 방향 → 변화량 범위: [{min_change:.2f}, {max_change:.2f}]"
            )
        else:
            # 감소 방향
            # 절댓값으로 계산: |remaining_change| - max_change × (remaining_steps - 1)
            abs_remaining = abs(remaining_change)
            min_change_abs = abs_remaining - (
                max_change_this_step * (remaining_steps - 1)
            )
            min_change_abs = max(0, min_change_abs)

            # 감소 방향이므로 음수로 변환
            max_change = -min_change_abs  # 최소한 이만큼은 감소해야 함
            min_change = max(-max_change_this_step, remaining_change)  # 최대 변화량 제한
            print(
                f"                감소 방향 → 변화량 범위: [{min_change:.2f}, {max_change:.2f}]"
            )

    # 6. 랜덤 변화량 선택
    random_change = random.uniform(min_change, max_change)
    new_value = prev_value + random_change
    print(f"                랜덤 변화량: {random_change:.2f} → 새 값: {new_value:.2f}")

    # 7. 새 값 반환
    return new_value


def interpolate_missing_data(
    df: pd.DataFrame,
    missing_timestamps: list[datetime],
    sensor_columns: list[str],
) -> pd.DataFrame:
    """누락된 시간대의 데이터를 랜덤 보간합니다 (목표값 도달 보장).

    Args:
        df: 원본 DataFrame
        missing_timestamps: 누락된 시간대 리스트
        sensor_columns: 보간할 센서 컬럼 리스트

    Returns:
        보간된 데이터가 추가된 DataFrame

    Raises:
        ValueError: 1%씩 변해도 목표값 도달 불가능한 경우
    """
    # 1. 누락 구간별로 그룹화
    missing_groups = []
    current_group = []

    for i, missing_time in enumerate(missing_timestamps):
        if not current_group:
            current_group.append(missing_time)
        else:
            # 이전 시간과 1시간 차이인지 확인
            if (missing_time - current_group[-1]).total_seconds() == 3600:
                current_group.append(missing_time)
            else:
                missing_groups.append(current_group)
                current_group = [missing_time]

    if current_group:
        missing_groups.append(current_group)

    # 2. 각 그룹별로 보간 수행
    interpolated_rows = []

    for group_idx, group in enumerate(missing_groups, start=1):

        # 2-1. 이 그룹의 이전/이후 데이터 찾기
        first_missing = group[0]
        last_missing = group[-1]

        prev_data = df[df["ACQU_TIME"] < first_missing].iloc[-1]
        next_data = df[df["ACQU_TIME"] > last_missing].iloc[0]

        missing_hours = len(group)

        # 2-2. 각 센서 컬럼별로 검증
        for col in sensor_columns:
            prev_value = prev_data[col]
            next_value = next_data[col]

            # 변동률 검증
            validate_variation_rate(prev_value, next_value, missing_hours, col)

        # 2-3. 그룹 내 각 시간대 보간
        current_values = {col: prev_data[col] for col in sensor_columns}

        for step, missing_time in enumerate(group, start=1):
            # 새 행 생성
            new_row = prev_data.copy()
            new_row["ACQU_TIME"] = missing_time

            # 각 센서 값 랜덤 보간
            for col in sensor_columns:
                interpolated_value = random_interpolate_with_guarantee(
                    current_values[col],
                    next_data[col],
                    missing_hours,
                    step,
                    col,
                )
                new_row[col] = round(interpolated_value, DECIMAL_PLACES)
                current_values[col] = new_row[col]

            new_row["DATA_SOURCE_TYPE"] = "ESTIMATED"
            interpolated_rows.append(new_row)

    # 3. 원본과 병합 후 정렬
    result = pd.concat([df, pd.DataFrame(interpolated_rows)], ignore_index=True)
    return result.sort_values("ACQU_TIME").reset_index(drop=True)


def generate_insert_sql(
    df: pd.DataFrame, constants: dict[str, Any], output_path: Path
) -> None:
    """INSERT SQL 문을 생성하여 파일로 저장합니다.

    Args:
        df: 데이터 DataFrame (보간된 데이터만 포함)
        constants: 상수 값들
        output_path: 출력 파일 경로
    """
    # DATA_SOURCE_TYPE이 ESTIMATED인 행만 필터링
    estimated_df = df[df["DATA_SOURCE_TYPE"] == "ESTIMATED"]

    if len(estimated_df) == 0:
        print("보간된 데이터가 없습니다.")
        return

    # 필수 컬럼 정의
    columns = [
        "EQUIP_SN",
        "ACQU_TIME",
        "MEAS_LAYER",
        "FARM_ID",
        "SITE_ID",
        "MEAS_DEPTH",
        "VAL_TP",
        "VAL_DO",
        "VAL_DS",
        "VAL_PH",
        "VAL_OR",
        "VAL_SL",
        "QC_TP",
        "QC_DO",
        "QC_DS",
        "QC_PH",
        "QC_OR",
        "QC_SL",
        "REGI_TIME",
        "DATA_SOURCE_TYPE",
    ]

    with open(output_path, "w", encoding="utf-8") as f:
        # 배치 단위로 INSERT ALL 구문 생성
        for batch_start in range(0, len(estimated_df), BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, len(estimated_df))
            batch = estimated_df.iloc[batch_start:batch_end]

            f.write("INSERT ALL\n")

            for _, row in batch.iterrows():
                # 1. 타임스탬프 변환 (REGI_TIME = ACQU_TIME)
                acqu_time = row["ACQU_TIME"].strftime("%Y-%m-%d %H:%M:%S.000000")

                # 2. 센서 값 (NULL 처리)
                def format_value(val: Any) -> str:
                    if pd.isna(val) or val == "":
                        return "NULL"
                    if isinstance(val, (int, float)):
                        return str(val)
                    return f"'{val}'"

                # 3. INSERT 구문 생성
                values = [
                    f"'{constants['EQUIP_SN']}'",
                    f"TIMESTAMP '{acqu_time}'",
                    format_value(row["MEAS_LAYER"]),
                    f"'{constants['FARM_ID']}'",
                    f"'{constants['SITE_ID']}'",
                    format_value(constants["MEAS_DEPTH"]),
                    format_value(row.get("VAL_TP")),
                    format_value(row.get("VAL_DO")),
                    format_value(row.get("VAL_DS")),
                    format_value(row.get("VAL_PH")),
                    format_value(row.get("VAL_OR")),
                    format_value(row.get("VAL_SL")),
                    "'O'",  # QC_TP
                    "'O'",  # QC_DO
                    "'O'",  # QC_DS
                    "'O'",  # QC_PH
                    "'O'",  # QC_OR
                    "'O'",  # QC_SL
                    f"TIMESTAMP '{acqu_time}'",  # REGI_TIME
                    "'ESTIMATED'",  # DATA_SOURCE_TYPE
                ]

                f.write(
                    f"\tINTO COM_SENSOR_DATA ({','.join(columns)}) "
                    f"VALUES ({','.join(values)})\n"
                )

            f.write("SELECT 1 FROM DUAL;\n\n")

    print(f"SQL 파일 생성 완료: {output_path}")
    print(f"보간된 데이터 개수: {len(estimated_df)}개")


def main() -> None:
    """메인 실행 함수"""
    try:
        print("=== 센서 데이터 보간 및 SQL 생성 시작 ===\n")

        # 1. CSV 파일 읽기
        print(f"1. CSV 파일 읽기: {INPUT_CSV_PATH}")
        df = load_csv(INPUT_CSV_PATH)
        print(f"   총 {len(df)}개 레코드 읽음\n")

        # 2. 필수 컬럼 검증
        print("2. 필수 컬럼 검증")
        constants = validate_constant_columns(df)
        for col, val in constants.items():
            print(f"   {col}: {val}")
        print()

        # 3. 시간 누락 감지
        print("3. 시간 누락 감지")
        missing_timestamps = identify_missing_timestamps(df)
        if missing_timestamps:
            missing_periods = format_missing_periods(missing_timestamps)
            total_hours = len(missing_timestamps)
            print(f"   누락된 시간대: {total_hours}시간")
            for start, end, count in missing_periods:
                if start == end:
                    # 단일 시간
                    print(f"   - {start.strftime('%Y-%m-%d %H:%M:%S')} (1시간)")
                else:
                    # 기간
                    print(
                        f"   - {start.strftime('%Y-%m-%d %H:%M:%S')} ~ "
                        f"{end.strftime('%Y-%m-%d %H:%M:%S')} ({count}시간)"
                    )
        else:
            print("   누락된 시간대 없음")
        print()

        # 4. 보간 대상 센서 컬럼 판단
        print("4. 보간 대상 센서 컬럼 판단")
        sensor_columns = check_sensor_columns(df)
        print(f"   보간 대상: {', '.join(sensor_columns)}\n")

        if not missing_timestamps:
            print("누락된 데이터가 없어 보간이 필요하지 않습니다.")
            return

        # 5. 데이터 보간
        print("5. 데이터 보간 수행")
        interpolated_df = interpolate_missing_data(
            df, missing_timestamps, sensor_columns
        )
        print(f"   보간 완료 (총 {len(interpolated_df)}개 레코드)\n")

        # 6. INSERT SQL 생성
        print("6. INSERT SQL 생성")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = OUTPUT_DIR / "insert_estimated_data.sql"
        generate_insert_sql(interpolated_df, constants, output_path)

        print("\n=== 처리 완료 ===")

    except Exception as e:
        print(f"\n오류 발생: {e}")
        raise


if __name__ == "__main__":
    main()
