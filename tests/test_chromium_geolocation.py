"""Check location permissions on an already authorized site using synthetic coordinates."""

import json
import os
from contextlib import ExitStack

import pytest
from playwright.sync_api import sync_playwright

from chromium.native import default_binary, probe


@pytest.mark.skipif(os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1", reason="needs the installed Chromium build")
def test_location_policy_overrides_saved_site_grants_across_restart(tmp_path):
    binary, _ = probe(default_binary())
    origin = "http://127.0.0.1:18179"
    directories = [tmp_path / name for name in ("A", "B")]
    for directory in directories:
        default = directory / "Default"
        default.mkdir(parents=True)
        exceptions = {
            "geolocation": {f"{origin},*": {"setting": 1}},
            "geolocation_with_options": {f"{origin},*": {"setting": {"approximate": 1, "precise": 1}}},
        }
        (default / "Preferences").write_text(
            json.dumps({"profile": {"content_settings": {"exceptions": exceptions}}}), encoding="utf-8")

    def read(page):
        return page.evaluate("""async () => {
          const geo = navigator.geolocation;
          const options = {maximumAge: 60000, timeout: 5000};
          const position = p => [p.coords.latitude, p.coords.longitude];
          const once = () => new Promise(resolve => geo.getCurrentPosition(
            p => resolve(position(p)), e => resolve(e.code), options));
          const watch = () => new Promise(resolve => {
            const finish = value => { geo.clearWatch(id); resolve(value); };
            const id = geo.watchPosition(p => finish(position(p)), e => finish(e.code), options);
          });
          const permission = await navigator.permissions.query({name:'geolocation'});
          return [permission.state, ...await Promise.all([once(), watch()])];
        }""")

    with sync_playwright() as playwright:
        for policies in ((False, False), (True, False), (False, True)):
            with ExitStack() as browsers:
                for directory, blocked in zip(directories, policies):
                    context = playwright.chromium.launch_persistent_context(
                        directory, executable_path=str(binary), headless=True,
                        args=["--moltskin-block-geolocation"] if blocked else [],
                        geolocation={"latitude": 12.25, "longitude": 34.5, "accuracy": 1},
                    )
                    browsers.callback(context.close)
                    # Give both environments the same origin grant independently of
                    # the host OS location prompt. Coordinates are entirely synthetic.
                    context.grant_permissions(["geolocation"], origin=origin)
                    page = context.pages[0]
                    page.route(origin + "/**", lambda route: route.fulfill(
                        status=200, content_type="text/html", body="<title>Local permission check</title>"))
                    page.goto(origin)
                    expected = ["denied", 1, 1] if blocked else ["granted", [12.25, 34.5], [12.25, 34.5]]
                    assert read(page) == expected, (directory.name, blocked)
                    if blocked:
                        context.clear_permissions()
                        assert read(page) == expected, "stored site permission must also respect the environment policy"
