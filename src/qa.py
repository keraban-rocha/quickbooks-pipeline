import uuid

import pandas as pd

from datetime import (
    datetime,
    timezone
)


def create_check(
    layer,
    check_name,
    passed,
    severity="ERROR",
    actual_value=None,
    expected_value=None,
    message=None,
    run_id=None
):

    return {
        "run_id":
            run_id or str(uuid.uuid4()),

        "checked_at":
            datetime.now(timezone.utc),

        "layer":
            layer,

        "check_name":
            check_name,

        "status":
            "PASS"
            if passed
            else "FAIL",

        "severity":
            severity,

        "actual_value":
            actual_value,

        "expected_value":
            expected_value,

        "message":
            message
    }


def check_not_empty(
    df,
    layer,
    name,
    run_id
):

    return create_check(
        layer=layer,
        check_name=name,
        passed=len(df) > 0,
        actual_value=len(df),
        expected_value="> 0",
        run_id=run_id
    )


def check_no_nulls(
    df,
    column,
    layer,
    run_id
):

    null_count = (
        df[column]
        .isna()
        .sum()
    )

    return create_check(
        layer=layer,
        check_name=f"{column}_not_null",
        passed=null_count == 0,
        actual_value=null_count,
        expected_value=0,
        run_id=run_id
    )


def check_unique(
    df,
    columns,
    layer,
    run_id
):

    duplicates = (
        df
        .duplicated(
            subset=columns
        )
        .sum()
    )

    return create_check(
        layer=layer,
        check_name=(
            "unique_"
            + "_".join(columns)
        ),
        passed=duplicates == 0,
        actual_value=duplicates,
        expected_value=0,
        run_id=run_id
    )


def check_journals_balanced(
    df,
    run_id
):

    balances = (
        df
        .groupby("journal_id")
        ["signed_amount"]
        .sum()
    )

    unbalanced = (
        balances.abs() >= 0.01
    ).sum()

    return create_check(
        layer="silver",
        check_name="journals_balanced",
        passed=unbalanced == 0,
        actual_value=int(unbalanced),
        expected_value=0,
        run_id=run_id
    )


def results_to_dataframe(results):

    return pd.DataFrame(
        results
    )


def raise_if_critical_failures(
    qa_results
):

    failures = [
        result
        for result in qa_results
        if (
            result["status"] == "FAIL"
            and
            result["severity"]
            == "ERROR"
        )
    ]

    if failures:

        names = [
            x["check_name"]
            for x in failures
        ]

        raise RuntimeError(
            f"Critical QA checks failed: {names}"
        )