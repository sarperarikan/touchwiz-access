# Copyright (C) 2026 AccessiMind Contributors
# This file is covered by the GNU General Public License version 2 or later.

"""Build the installable TouchWiz Access add-on."""

import re
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po

SOURCE = Path(__file__).resolve().parent
PACKAGE_NAME = "touchwiz-access.nvda-addon"
_VERSIONED_PACKAGE = re.compile(r"touchwiz-access-\d+\.\d+\.\d+\.nvda-addon")


def _writePackage(package: Path) -> None:
	with ZipFile(package, "w", compression=ZIP_DEFLATED) as archive:
		archive.write(SOURCE / "COPYING.txt", "COPYING.txt")
		archive.write(SOURCE / "manifest.ini", "manifest.ini")
		for pattern in ("globalPlugins/**/*.py", "doc/*/*.html", "doc/*.css", "locale/*/manifest.ini"):
			for path in sorted(SOURCE.glob(pattern)):
				archive.write(path, path.relative_to(SOURCE).as_posix())
		for path in sorted(SOURCE.glob("locale/*/LC_MESSAGES/nvda.po")):
			with path.open("rb") as source:
				catalog = read_po(source, locale=path.parents[1].name, abort_invalid=True)
			compiled = BytesIO()
			write_mo(compiled, catalog)
			archive.writestr(path.relative_to(SOURCE).with_suffix(".mo").as_posix(), compiled.getvalue())


def build(outputDir: Path) -> Path:
	outputDir.mkdir(parents=True, exist_ok=True)
	package = outputDir / PACKAGE_NAME
	with TemporaryDirectory(prefix=".touchwiz-access-", dir=outputDir) as stagingDir:
		staged = Path(stagingDir) / PACKAGE_NAME
		_writePackage(staged)
		with ZipFile(staged) as archive:
			if invalidMember := archive.testzip():
				raise ValueError(f"Invalid package member: {invalidMember}")
		staged.replace(package)

	for oldPackage in outputDir.glob("touchwiz-access-*.nvda-addon"):
		if _VERSIONED_PACKAGE.fullmatch(oldPackage.name) and oldPackage.is_file():
			oldPackage.unlink(missing_ok=True)
	return package


if __name__ == "__main__":
	print(build(SOURCE / "dist"))
