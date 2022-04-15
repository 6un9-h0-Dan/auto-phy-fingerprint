FROM python:3

WORKDIR /code

#avoid questions when installing stuff in apt-get
ARG DEBIAN_FRONTEND=noninteractive

#TODO: need an old version of h5py so it's compatible with my laptop for easy viewing during development -- use latest hdf5/h5py when I have a modern install :)
RUN apt-get update && apt-get install -y libhdf5-103 libhdf5-dev

#install pip dependencies (inc. building *old* h5py)
RUN pip3 install numpy matplotlib pyModeS h5py==2.10.0 zmq

COPY code/store.py /code

ENTRYPOINT ["python3", "store.py"]

