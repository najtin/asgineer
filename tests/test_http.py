import requests
import pytest

import asgish

from common import get_backend, AsgishServerProcess


def test_backend_reporter(capsys=None):
    """ A stub test to display the used backend.
    """
    msg = f"  Running tests with ASGI server: {get_backend()}"
    if capsys:
        with capsys.disabled():
            print(msg)
    else:
        print(msg)


## Test normal usage


async def handler1(request):
    return 200, {"xx-foo": "x"}, "hi!"


async def handler2(request):
    async def handler1(request):
        return 200, {"xx-foo": "x"}, "hi!"

    return await handler1(request)


async def handler3(request):
    async def handler1(request):
        return 200, {"xx-foo": "x"}, "hi!"

    async def handler2(request):
        return await handler1(request)

    return await handler2(request)


async def handler4(request):
    return "ho!"


async def handler5(request):
    return ("ho!",)


async def handler6(request):
    return 400, "ho!"  # Invalid


async def handler7(request):
    return {"xx-foo": "x"}, "ho!"


async def handler_json1(request):
    return {"foo": 42, "bar": 7}


async def handler_html1(request):
    return "<!DOCTYPE html> <html>foo</html>"


async def handler_html2(request):
    return "<html>foo</html>"


def test_normal_usage():

    # Test normal usage

    with AsgishServerProcess(handler1) as p:
        res = requests.get(p.url)

    # res.status_code, res.reason, res.headers, , res.content
    print(res.content)
    print(res.headers)
    print(p.out)

    assert res.status_code == 200
    assert res.content.decode() == "hi!"
    assert not p.out

    # Daphne capitalizes the header keys, hypercorn aims at lowercase
    refheaders = {"content-type", "content-length", "xx-foo"}
    if get_backend() not in "daphne":
        refheaders.update({"server", "date"})
    assert set(k.lower() for k in res.headers.keys()) == refheaders
    assert res.headers["content-type"] == "text/plain"
    assert res.headers["content-length"] == "3"  # yes, a string

    # Test delegation to other handler

    with AsgishServerProcess(handler2) as p:
        res = requests.get(p.url)

    assert res.status_code == 200
    assert res.content.decode() == "hi!"
    assert not p.out
    assert "xx-foo" in res.headers

    # Test delegation to yet other handler

    with AsgishServerProcess(handler3) as p:
        res = requests.get(p.url)

    assert res.status_code == 200
    assert res.content.decode() == "hi!"
    assert not p.out
    assert "xx-foo" in res.headers


def test_output_shapes():

    # Singleton arg

    with AsgishServerProcess(handler4) as p:
        res = requests.get(p.url)

    assert res.status_code == 200
    assert res.content.decode() == "ho!"
    assert not p.out

    with AsgishServerProcess(handler5) as p:
        res = requests.get(p.url)

    assert res.status_code == 200
    assert res.content.decode() == "ho!"
    assert not p.out

    # Two element tuple (two forms, one is flawed)

    with AsgishServerProcess(handler6) as p:
        res = requests.get(p.url)

    assert res.status_code == 500
    assert "Headers must be a dict" in res.content.decode()
    assert "Headers must be a dict" in p.out

    with AsgishServerProcess(handler7) as p:
        res = requests.get(p.url)

    assert res.status_code == 200
    assert res.content.decode() == "ho!"
    assert not p.out
    assert "xx-foo" in res.headers


def test_body_types():

    # Plain text

    with AsgishServerProcess(handler4) as p:
        res = requests.get(p.url)

    assert res.status_code == 200
    assert res.headers["content-type"] == "text/plain"
    assert res.content.decode()
    assert not p.out

    # Json

    with AsgishServerProcess(handler_json1) as p:
        res = requests.get(p.url)

    assert res.status_code == 200
    assert res.headers["content-type"] == "application/json"
    assert res.json() == {"foo": 42, "bar": 7}
    assert not p.out

    # HTML

    with AsgishServerProcess(handler_html1) as p:
        res = requests.get(p.url)

    assert res.status_code == 200
    assert res.headers["content-type"] == "text/html"
    assert "foo" in res.content.decode()
    assert not p.out

    with AsgishServerProcess(handler_html2) as p:
        res = requests.get(p.url)

    assert res.status_code == 200
    assert res.headers["content-type"] == "text/html"
    assert "foo" in res.content.decode()
    assert not p.out


## Chunking


async def handler_chunkwrite1(request):
    async def asynciter():
        yield "foo"
        yield "bar"

    return 200, {}, asynciter()


async def handler_chunkread1(request):
    body = []
    async for chunk in request.iter_body():
        body.append(chunk)
    return b"".join(body)


async def handler_chunkread2(request):
    return request.iter_body()  # echo :)


def test_chunking():

    # Write

    with AsgishServerProcess(handler_chunkwrite1) as p:
        res = requests.get(p.url)

    assert res.status_code == 200
    assert res.content.decode() == "foobar"
    assert not p.out

    # Read

    with AsgishServerProcess(handler_chunkread1) as p:
        res = requests.post(p.url, b"foobar")

    assert res.status_code == 200
    assert res.content.decode() == "foobar"
    assert not p.out

    # Both

    with AsgishServerProcess(handler_chunkread2) as p:
        res = requests.post(p.url, b"foobar")

    assert res.status_code == 200
    assert res.content.decode() == "foobar"
    assert not p.out


## Test exceptions and errors


async def handler_err1(request):
    return 501, {"xx-custom": "xx"}, "oops"


async def handler_err2(request):
    raise ValueError("wo" + "ops")
    return 200, {"xx-custom": "xx"}, "oops"


async def handler_err3(request):
    async def chunkiter():
        raise ValueError("wo" + "ops")
        yield "foo"

    return 200, {"xx-custom": "xx"}, chunkiter()


async def handler_err4(request):
    async def chunkiter():
        yield "foo"
        raise ValueError("wo" + "ops")  # too late to do a status 500

    return 200, {"xx-custom": "xx"}, chunkiter()


def test_errors():

    # Explicit error

    with AsgishServerProcess(handler_err1) as p:
        res = requests.get(p.url)

    assert not res.ok
    assert res.status_code == 501
    assert res.content.decode() == "oops"
    assert not p.out
    assert "xx-custom" in res.headers

    # Exception in handler

    with AsgishServerProcess(handler_err2) as p:
        res = requests.get(p.url)

    assert not res.ok
    assert res.status_code == 500
    assert "error in request handler" in res.content.decode().lower()
    assert "woops" in res.content.decode()
    assert "woops" in p.out
    assert p.out.count("ERROR") == 1
    assert p.out.count("woops") == 2
    assert "xx-custom" not in res.headers

    # Exception in handler with chunked body

    with AsgishServerProcess(handler_err3) as p:
        res = requests.get(p.url)

    assert not res.ok
    assert res.status_code == 500
    assert "error in chunked response" in res.content.decode().lower()
    assert "woops" in res.content.decode()
    assert "woops" in p.out and "foo" not in p.out
    assert "xx-custom" not in res.headers

    # Exception in handler with chunked body, too late

    with AsgishServerProcess(handler_err4) as p:
        res = requests.get(p.url)

    assert res.ok  # no fail, just got half the page ...
    assert res.status_code == 200
    assert res.content.decode() == "foo"
    assert "woops" in p.out
    assert "xx-custom" in res.headers


## Test wrong output


async def handler_output1(request):
    return 200, {}, "foo", "bar"


async def handler_output2(request):
    return 0


async def handler_output3(request):
    return [200, {}, "foo"]


async def handler_output4(request):
    return "200", {}, "foo"


async def handler_output5(request):
    return 200, 4, "foo"


async def handler_output6(request):
    return 200, {}, 4


async def handler_output11(request):
    async def chunkiter():
        yield 3
        yield "foo"

    return 200, {"xx-custom": "xx"}, chunkiter()


async def handler_output12(request):
    async def chunkiter():
        yield "foo"
        yield 3  # too late to do a status 500

    return 200, {"xx-custom": "xx"}, chunkiter()


def test_wrong_output():

    with AsgishServerProcess(handler_output1) as p:
        res = requests.get(p.url)

    assert res.status_code == 500
    assert "handler returned 4-tuple" in res.content.decode().lower()
    assert "handler returned 4-tuple" in p.out.lower()

    for handler in (handler_output2, handler_output3, handler_output6):
        with AsgishServerProcess(handler_output2) as p:
            res = requests.get(p.url)

        assert res.status_code == 500
        assert "body cannot be" in res.content.decode().lower()
        assert "body cannot be" in p.out.lower()

    with AsgishServerProcess(handler_output4) as p:
        res = requests.get(p.url)

    assert res.status_code == 500
    assert "status code must be an int" in res.content.decode().lower()
    assert "status code must be an int" in p.out.lower()

    with AsgishServerProcess(handler_output5) as p:
        res = requests.get(p.url)

    assert res.status_code == 500
    assert "headers must be a dict" in res.content.decode().lower()
    assert "headers must be a dict" in p.out.lower()

    # Chunked

    with AsgishServerProcess(handler_output11) as p:
        res = requests.get(p.url)

    assert res.status_code == 500
    assert "error in chunked response" in res.content.decode().lower()
    assert "body chunk must be" in res.content.decode().lower()
    assert "body chunk must be" in p.out.lower()

    with AsgishServerProcess(handler_output12) as p:
        res = requests.get(p.url)

    assert res.status_code == 200  # too late to set status!
    assert res.content.decode() == "foo"
    assert "body chunk must be" in p.out.lower()


## Test wrong usage


def handler_wrong_use1(request):
    return 200, {}, "hi"


async def handler_wrong_use2(request):
    yield 200, {}, "hi"


def test_wrong_use():

    with pytest.raises(TypeError):
        asgish.to_asgi(handler_wrong_use1)

    with pytest.raises(TypeError):
        asgish.to_asgi(handler_wrong_use2)


##

if __name__ == "__main__":
    from common import run_tests, set_backend_from_argv

    set_backend_from_argv()

    run_tests(globals())

    # with AsgishServerProcess(handler_err2) as p:
    #     time.sleep(10)
