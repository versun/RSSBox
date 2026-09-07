from django.http import JsonResponse, StreamingHttpResponse
from feed2json import feed2json


def build_feed_response(atom_feed, filename, format="xml"):
    if format == "json":
        if not atom_feed:
            return JsonResponse({"error": "No feed data available"}, status=404)
        return JsonResponse(feed2json(atom_feed))

    def stream_content():
        if not atom_feed:
            yield b"<error>No feed data available</error>"
            return
        chunk_size = 4096
        for index in range(0, len(atom_feed), chunk_size):
            yield atom_feed[index : index + chunk_size]

    response = StreamingHttpResponse(
        stream_content(),
        content_type="application/xml; charset=utf-8",
    )
    response["Content-Disposition"] = f"inline; filename={filename}.xml"
    return response
