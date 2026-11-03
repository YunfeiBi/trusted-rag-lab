"""Bounded child-process PDF preparation/parsing. Password travels via IPC only."""
import logging
import multiprocessing as mp
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from .ingestion import IngestionError, parse_and_chunk


def _child(connection, operation, source, destination, limits):
    logging.disable(logging.CRITICAL)
    try:
        password = connection.recv()  # Never command line, environment, disk or DB.
        if operation == 'prepare':
            reader = PdfReader(source)
            decrypted = False
            if reader.is_encrypted:
                if not reader.decrypt(password or ''):
                    raise IngestionError('PDF_PASSWORD_INCORRECT' if password else 'PDF_PASSWORD_REQUIRED')
                decrypted = True
            password = None
            if len(reader.pages) > limits['max_pages']:
                raise IngestionError('TOO_MANY_PAGES')
            if decrypted:
                writer = PdfWriter(clone_from=reader)
                writer.write(destination)
                if Path(destination).stat().st_size > limits['max_bytes']:
                    raise IngestionError('FILE_TOO_LARGE')
            connection.send(('ok', decrypted))
        else:
            parts = parse_and_chunk(Path(source), Path(source).suffix, max_pages=limits['max_pages'], max_chars=limits['max_chars'], max_chunks=limits['max_chunks'])
            connection.send(('ok', parts))
    except IngestionError as exc:
        connection.send(('error', exc.code))
    except Exception:
        connection.send(('error', 'INVALID_PDF' if operation == 'prepare' else 'PARSE_FAILED'))
    finally:
        connection.close()


def isolated(operation, source, settings, *, destination=None, password=None, timeout=None):
    context = mp.get_context('spawn')
    parent, child = context.Pipe()
    limits = {'max_pages': settings.max_pdf_pages, 'max_chars': settings.max_text_chars, 'max_chunks': settings.max_chunks, 'max_bytes': settings.max_upload_bytes}
    process = context.Process(target=_child, args=(child, operation, str(source), str(destination) if destination else None, limits), daemon=True)
    try:
        process.start()
        child.close()
        parent.send(password)
        password = None
        if not parent.poll(timeout if timeout is not None else settings.parse_timeout_seconds):
            raise IngestionError('PROCESSING_TIMEOUT')
        try:
            state, result = parent.recv()
        except EOFError as exc:
            raise IngestionError('PARSE_FAILED') from exc
        if state != 'ok':
            raise IngestionError(result)
        return result
    finally:
        parent.close()
        child.close()
        if process.pid:
            process.join(timeout=.2)
            if process.is_alive():
                process.terminate()
                process.join(timeout=3)
            if process.is_alive():
                process.kill()
                process.join()
