def ui(request):
    return {"lang": getattr(request, "lang", "en")}
