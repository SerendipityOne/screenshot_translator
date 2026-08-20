# Third-Party Notices

The packaged application includes the following third-party software. The
application keeps Qt and the other native components as dynamically linked
files inside `_internal/`.

| Component | License | Project |
| --- | --- | --- |
| Python | PSF License 2.0 | <https://www.python.org/> |
| PySide6, shiboken6 and Qt 6 | LGPL-3.0-only, with alternative GPL/commercial terms | <https://doc.qt.io/qtforpython-6/licenses.html> |
| python-xlib | LGPL-2.1-or-later | <https://github.com/python-xlib/python-xlib> |
| PyInstaller bootloader | GPL-2.0-or-later with Bootloader Exception | <https://pyinstaller.org/en/stable/license.html> |
| OpenSSL (libssl/libcrypto) | Apache-2.0 | <https://www.openssl.org/source/license.html> |
| X.Org/XCB libraries, including xcb-util-cursor | MIT/X11 family licenses | <https://www.x.org/releases/current/doc/xorg-docs/License.html> |

Qt/PySide6 are used under the LGPL terms. Their shared libraries are not
modified and can be replaced in the application directory. Copyright and
license texts supplied by those projects remain applicable.

Tesseract OCR and its language data are system dependencies and are not
included in this archive.
