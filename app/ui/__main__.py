import multiprocessing

from app.ui.desktop import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
