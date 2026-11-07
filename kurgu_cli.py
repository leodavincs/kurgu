"""The `kurgu` command: `kurgu doctor`, `kurgu setup-ffmpeg`, `kurgu notes ...`, otherwise the editor server (`kurgu <project>`)."""
import sys


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    if args and args[0] == "doctor":
        import doctor
        sys.exit(doctor.main(args[1:]))
    if args and args[0] == "setup-ffmpeg":
        import ffmpeg_fetch
        sys.exit(ffmpeg_fetch.main(args[1:]))
    import server
    return server.main(args)   # also handles `notes`


if __name__ == "__main__":
    main()
