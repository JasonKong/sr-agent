# Shared pytest fixtures live in packages so every package's tests can use them.
# Keep this the only conftest.py: mypy and pytest identify test-side modules by file name.
pytest_plugins = ["sr_db.testing"]
