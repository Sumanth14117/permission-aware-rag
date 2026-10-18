from tests.db_safety import (
    build_verified_engine,
    connect_and_verify,
    read_database_urls,
)


def main() -> None:
    development_url, test_url = read_database_urls()

    engine = build_verified_engine(
        development_url,
        test_url,
    )

    try:
        connect_and_verify(engine)
        print("Test database connection successful.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()