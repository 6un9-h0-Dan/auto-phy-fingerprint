import sys
import os

import numpy as np
import matplotlib.pyplot as plt

from pySDRBurstfile import *
import pylibmodes

import h5py
import pyModeS as pms

def decimate(z, DECIMATION):
	x = np.zeros(len(z)//DECIMATION, dtype=np.complex64)
	for i in range(DECIMATION):
		x += z[i::DECIMATION][:len(x)]
	x /= DECIMATION
	return x

def to_rtl_uint8(x):
	x = x.view(np.float32)
	x *= 128.0
	x += 127.0
	x = x.astype(np.uint8)
	return x

###########################


if len(sys.argv) != 2:
	print("Usage: {} <input file>".format(sys.argv[0]))
	exit(1)

entered_filename = sys.argv[1]

#sort out filename mangling
(inf_path, inf_name) = os.path.split(entered_filename)
(inf_name, inf_ext) = os.path.splitext(inf_name)

infilename = os.path.join(inf_path, inf_name) + inf_ext
interfilename = os.path.join(inf_path, inf_name) + "-intermediate" + ".hdf5"
outfilename = os.path.join(inf_path, inf_name) + "-df17" + ".hdf5"


#count the number of bursts
bfr = SDRBurstFileReader(infilename)
burst_count = 0
for (b, bm) in bfr.read():
	burst_count += 1
bfr.close()
print("Burst count: {}".format(burst_count))

#create the intermediate file
h5int = h5py.File(interfilename, "w")

bfr = SDRBurstFileReader(infilename)


count_pre, count_phase, count_demod_err, count_good = 0,0,0,0

msgtype_counts = {}
msgtype_successes = {}

burst_i = 0
h5int_i = 0
for (b, bm) in bfr.read():
	
	if len(b) < 19200:										# 240 * 10x decimation * 8 bytes complex64
		continue											#could pad with zeros instead to get short bursts out (e.g. 56-bit ones, which might still be interesting)
	
	z = np.frombuffer(b, dtype=np.complex64)
	DECIMATION = 10
	x = decimate(z, DECIMATION)
	
	#x -= np.mean(x)										#remove DC (seemed to decrease successful decoding and can't be bothered to debug)
	x /= np.max(np.abs(x))									#normalise to use the full quantised range
	
	x = to_rtl_uint8(x)

	#set up decoding
	state = pylibmodes.mode_s_t()
	pylibmodes.mode_s_init(state)

	res = pylibmodes.mode_s_detect_result()

	pylibmodes.mode_s_detectfirst(state, res, x)
	if res.processing_error != 0:
		raise ValueError("Error occurred during processing by libmodes (e.g. burst too short)")
			
	count_pre, count_phase, count_demod_err, count_good = count_pre + res.preamble_found, count_phase + res.phase_corrected, count_demod_err + res.demod_error_count, count_good + res.good_message

	#if res.preamble_found == 1:
	#	print("err: {}, offset: {}, preamble_found: {}, phase_corrected: {}, demod_error_count: {}, delta_test_result: {}, good_message: {}, msgtype: {}, msglen: {}".format(res.processing_error, res.offset, res.preamble_found, res.phase_corrected, res.demod_error_count, res.delta_test_result, res.good_message, res.msgtype, res.msglen))

	#record success stats
	if res.preamble_found == 1 and res.demod_error_count == 0 and res.delta_test_result == 1:
		msgtype_counts[res.msgtype] = 1 if res.msgtype not in msgtype_counts else msgtype_counts[res.msgtype] + 1
		msgtype_successes[res.msgtype] = res.good_message if res.msgtype not in msgtype_successes else msgtype_successes[res.msgtype] + res.good_message

		##print hex of df17 messages with good crcs
		#if res.msgtype == 17 and res.good_message == 1:
		#	print(pylibmodes.get_mm_msg(res.mm.msg).hex())

	#extract relevant subsection of burst to save
	if res.good_message == 1 and res.msgtype == 17:
		sec_start = res.offset * 2										#2 byte-values per sample
		sec_end = res.offset * 2 + (res.msglen * 8 * 2 + 16) * 2		#8 bits in msg, 2 samples per bit, 16 samples of preamble, 2 byte-values per sample
		#print(sec_start, sec_end, sec_end - sec_start)

		orig_start = res.offset * DECIMATION							#original is complex64, so no need to double as there is with data passed into libmodes
		orig_end = orig_start + (res.msglen * 8 * 2 + 16) * DECIMATION

		yz = z[orig_start:orig_end]
		h5int["bursts_"+str(h5int_i)] = yz
		h5int["bursts_"+str(h5int_i)].attrs["libmodes_datahex"] = pylibmodes.get_mm_msg(res.mm.msg).hex()
		h5int_i += 1

		#print("input: {},{} ({}) -> raw: {},{} ({})".format(sec_start,sec_end,sec_end-sec_start, orig_start, orig_end, orig_end-orig_start))
		#
		#y = x[sec_start:sec_end]										#should be 480 bytes long, to become 240 complex samples
		#y = ((y.astype(np.float32) - 127.0) / 128.0).view(np.complex64)
		##yz = z[sec_start//2*DECIMATION:sec_end//2*DECIMATION]
		#yz = z[orig_start:orig_end]
		#plt.subplot(2,1,1)
		#plt.plot(np.abs(y))
		#plt.subplot(2,1,2)
		#plt.plot(np.abs(yz))
		#plt.show()

	#do a check whether there are any other messages
	rest_of_burst = x[res.offset*2+(res.msglen*8*2+16):]
	#print(("rest", len(rest_of_burst)))
	if len(rest_of_burst) >= 480:
		res = pylibmodes.mode_s_detect_result()
		if res.good_message == 1:
			x = x[res.offset*2+(res.msglen*8*2+16):]
		else:
			x = x[res.offset*2+2:]
		pylibmodes.mode_s_detectfirst(state, res, x)
		if res.preamble_found == 1:
			print("Warning: Other preamble(s) found (but discarded) after first in burst {}".format(burst_i))
		
	burst_i += 1

	
h5int.close()
bfr.close()
del bfr

print("Preambles       : " + str(count_pre))
print("Phase correct   : " + str(count_phase))
print("Demod errors    : " + str(count_demod_err))
print("Usable messages : " + str(count_good))

msgtype_total = sum([x[1] for x in msgtype_counts.items()])

for mt in msgtype_counts:
	mts = msgtype_successes[mt]
	mtc = msgtype_counts[mt]
	mtcp = mtc / msgtype_total
	if mtcp > 0.1:
		print("Type {}: count = {} / {} ({:.2f}%), success = {} / {} ({:.2f}%)".format(mt, mtc, msgtype_total, 100.0*mtcp, mts, mtc, 100.0*mts/mtc))
		



#create final output file with single dataset
h5int = h5py.File(interfilename, "r")
h5out = h5py.File(outfilename, "w")
#save for non-siamese scripts format
#h5out.create_dataset("bursts", (h5int_i, 2400), dtype=np.complex64)
#h5out.create_dataset("libmodes_datahex", (h5int_i, 1), dtype="S28")
#for h5out_i in range(h5int_i):
#	h5out["bursts"][h5out_i,:] = h5int["bursts_"+str(h5out_i)]
#	h5out["libmodes_datahex"][h5out_i,:] = np.string_(h5int["bursts_"+str(h5out_i)].attrs["libmodes_datahex"])

#save for siamese script
h5out.create_dataset("inds", (h5int_i, 2400, 2), dtype=np.float32)
h5out.create_dataset("outds", (h5int_i, 1), dtype="S6")
for h5out_i in range(h5int_i):
	burst = h5int["bursts_"+str(h5out_i)]
	h5out["inds"][h5out_i,:,0] = np.real(burst)
	h5out["inds"][h5out_i,:,1] = np.imag(burst)
	h5out["outds"][h5out_i,:] = np.string_(pms.icao(h5int["bursts_"+str(h5out_i)].attrs["libmodes_datahex"]))
h5out.close()
h5int.close()


#delete the intermediate file
os.remove(interfilename)


#####old code to find as many message as possible in a burst
#	#find as many bursts as possible
#	res.preamble_found = 1						#little hack to star the loop (values reset each round)
#	while len(x) >= 480 and res.preamble_found == 1:		
#		pylibmodes.mode_s_detectfirst(state, res, x)
#		if res.processing_error != 0:
#			raise ValueError("problem!")
#			
#		count_pre, count_phase, count_demod_err, count_good = count_pre + res.preamble_found, count_phase + res.phase_corrected, count_demod_err + res.demod_error_count, count_good + res.good_message
#			
#		#if res.good_message == 1:
#		#	print("err: {}, offset: {}, preamble_found: {}, phase_corrected: {}, demod_error_count: {}, delta_test_result: {}, good_message: {}, msgtype: {}, msglen: {}".format(res.processing_error, res.offset, res.preamble_found, res.phase_corrected, res.demod_error_count, res.delta_test_result, res.good_message, res.msgtype, res.msglen))
#
#		#this can work but needs further changes
#		#if res.good_message == 1:
#		#	print(res.msgtype)
#		#	print(res.msglen)
#		#	print(res.mm.msg)
#		#	#print(pylibmodes.get_mm_msg(res.mm))
#		#	v = pylibmodes.get_mm_msg(res.mm.msg)
#		#	print(v.hex())
#		#	exit()
#			
#		#if res.preamble_found == 1:
#		#	print("err: {}, offset: {}, preamble_found: {}, phase_corrected: {}, demod_error_count: {}, delta_test_result: {}, good_message: {}, msgtype: {}, msglen: {}".format(res.processing_error, res.offset, res.preamble_found, res.phase_corrected, res.demod_error_count, res.delta_test_result, res.good_message, res.msgtype, res.msglen))
#		#	plt.subplot(2,1,1)
#		#	plt.plot(np.abs( ((x.astype(np.float32)-127.0)/128.0).view(np.complex64) ))
#		#	plt.subplot(2,1,2)
#		#	plt.plot(np.abs(z))
#		#	plt.show()
#		
#		if res.preamble_found == 1 and res.demod_error_count == 0 and res.delta_test_result == 1:
#			msgtype_counts[res.msgtype] = 1 if res.msgtype not in msgtype_counts else msgtype_counts[res.msgtype] + 1
#			msgtype_successes[res.msgtype] = res.good_message if res.msgtype not in msgtype_successes else msgtype_successes[res.msgtype] + res.good_message
#			
#			if res.msgtype == 17 and res.good_message == 1:
#				print(pylibmodes.get_mm_msg(res.mm.msg).hex())
#		
#		
#		
#		if res.good_message == 1 and res.msgtype == 17:
#			sec_start = res.offset * 2										#2 byte-values per sample
#			sec_end = res.offset * 2 + (res.msglen * 8 * 2 + 16) * 2		#8 bits in msg, 2 samples per bit, 16 samples of preamble, 2 byte-values per sample
#			#print(sec_start, sec_end, sec_end - sec_start)
#			
#			orig_start = res.offset * DECIMATION							#original is complex64, so no need to double as there is with data passed into libmodes
#			orig_end = orig_start + (res.msglen * 8 * 2 + 16) * DECIMATION
#			
#			yz = z[orig_start:orig_end]
#			h5out["bursts"]
#			
#			#print("input: {},{} ({}) -> raw: {},{} ({})".format(sec_start,sec_end,sec_end-sec_start, orig_start, orig_end, orig_end-orig_start))
#			#
#			#y = x[sec_start:sec_end]										#should be 480 bytes long, to become 240 complex samples
#			#y = ((y.astype(np.float32) - 127.0) / 128.0).view(np.complex64)
#			##yz = z[sec_start//2*DECIMATION:sec_end//2*DECIMATION]
#			#yz = z[orig_start:orig_end]
#			#plt.subplot(2,1,1)
#			#plt.plot(np.abs(y))
#			#plt.subplot(2,1,2)
#			#plt.plot(np.abs(yz))
#			#plt.show()
#		
#		#skip onwards past this message	
#		if res.good_message == 1:
#			#x = x[res.offset*2+480:]
#			x = x[res.offset*2+(res.msglen*8*2+16):]
#		else:
#			x = x[res.offset*2+2:]