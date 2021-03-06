"""
Natural System Components
@date   10/14/2016
@author: lixi729
@email: xinya.li@pnl.gov
@Project: Xanthos V1.0

License:  BSD 2-Clause, see LICENSE and DISCLAIMER files
Copyright (c) 2017, Battelle Memorial Institute
'''
'''
Water Management Components
@date   08/22/2021
@authors: Guta Wakbulcho Abeshu : gwabeshu@uh.edu
          University of Houston   
          HongYi Li : hli57@uh.edu
          University of Houston                
"""

import os
import math
import scipy.io as scio
import pandas as pd
import numpy as np
import scipy.sparse as sparse

def streamrouting(L, S0, F0, ChV, q, area, nday, dt, UM, UP, Sini_byr, wdirr, irrmean, 
                  mtifl, ppose, cpa,Release_policy, maxTurbineFlow, WConsumption, 
				  alpha, Sini_resv, res_flag, grdc_us_grids):
    """
    Runoff routing with water management

    L:    flow distance (m)                                        = (N x 1)
    S0:   initial channel storage value for the month (m^3)        = (N x 1)
    F0:   initial channel flow value (instantaneous) (m^3/s)       = (N x 1)
    ChV:  channel velocity (m/s)                                   = (N x 1)
    q:    runoff (mm/month)                                        = (N x 1)
    area: cell area (km^2)                                         = (N x 1)
    nday: number of days in the month                              = (1 x 1)
    dt:   size of the fixed time step (s)                          = (1 x 1)
    UM:   connection matrix (see notes in upstream_genmatrix)      = (N x N, sparse)
    Release_policy: policy look up table for Hydropower release
    maxTurbineFlow: maximum turbine flow
    WConsumption: water consumption
    alpha : reservoir capacity reduction coeffitient
    Sini_byr : initial reservoir storage at begining of the year
    Sini_resv: reservoir storage at end of preceeding month
    wdirr: irrigation demand
    irrmean: mean irrigation demand
    mtifl : mean total inflow
    ppose : reservoir purpose
    cpa: reservoir capacity
    res_flag: 0 for no water management and 1 for water management

    Outputs:
    S:     channel storage, unit m3
    Favg:  monthly average channel flow, unit m3/s
    F:     instantaneous channel flow, unit m3/s
    Sending : reservoir storage at end of month, unit m3
    Qin_res_avg: reservoir inflow, unit m3/s
    Qout_res_avg : reservoir outflow, unit m3/s
    """

    N = L.shape[0]                   # number of cells
    nt = int(nday * 24 * 3600 / dt)  # number of time steps

    # Initialite
    S = np.copy(S0) 
    F = np.copy(F0)
    Favg = np.zeros((N,), dtype=float)            # Mean Channel flow
    Qin_res_avg = np.zeros((N,), dtype=float)     # Inflow to reservoir
    Qout_res_avg = np.zeros((N,), dtype=float)    # Outflow from reservoir
    Qin_Channel_avg = np.zeros((N,), dtype=float) # Inflow to Channel
    Qout_channel_avg = np.zeros((N,), dtype=float)# Outflow from Channel
    Sending= np.zeros((N,), dtype=float)# Outflow from Channel

    # inverse of residence time
    tauinv = np.divide(ChV, L)   
    dtinv = 1.0 / dt
    # reshape the water management input data
    ppose = np.reshape(ppose,(N,))
    cpa   = np.reshape(cpa,(N,))
    unit_conversion_demand = area*1e3 / (nday*24*3600)
    monthly_demand   = np.reshape(np.multiply(wdirr,unit_conversion_demand),(N,)) #downstream  demand: m^3/s
    mean_demand  = np.reshape(np.multiply(irrmean,unit_conversion_demand),(N,))   #downstream mean demand:  m^3/s
    mtifl = np.reshape(mtifl,(N,))

    # classification based on purpose
    condH = np.where(ppose ==1)[0] # Hydropower reservoir cells	
    condI = np.where(ppose ==2)[0] # irrigation reservoir cells
    condF = np.where(ppose ==3)[0] # flood reservoir cells
    cond_all= np.where(ppose>0)[0]
	# grid water consumption
    if res_flag==1: 
        # water consumption
        qq = q - WConsumption
        # unmet consumption
        Wunmet = qq < 0
        # set excess runoff to zero for unmet cells
        qq[Wunmet] = 0 
    else:
        qq = q
    erlateral = (qq * area) * (1e3) / (nday * 24 * 3600)    # q -> erlateral: mm/month to m^3/s  
    # initialiaze for release   and reservoir storage
    Rres = np.zeros((N,), dtype = float) # final release 
   
    # routing at 3hr time step 
    for t in range(nt):
        # Channel Storage Balance      
        # compute trial steps for F, S
        F = S * tauinv               # vector dot multiply
        dSdt= UM.dot(F) + erlateral  # vector
        Qin = UP.dot(F) + erlateral  # vector  

        Sin = S.copy()        
        # Have to check for any flows that will be greater than the actual amount of water available.
        Sx = ((dSdt * dt)+S < 0 )     # logic

        if Sx.any():
            # For cells with excess flow, let the flow be all the water available:  inbound + lateral + storage.
            # Since dSdt = inbound + lateral - F, we can get the new, adjusted value by adding to dSdt the F
            #  we calculated above and adding to that S / dt
            F[Sx] = Qin[Sx] + S[Sx] * dtinv

            # The new F is all the inflow plus all the storage, so the final value of S is zero.
            dSdt[Sx] = -S[Sx]* dtinv
            S[Sx]    = 0
            
            # For the rest of the cells, recalculate dSdt using the updated fluxes
            # (some of them will have changed due to the changes above.
            Sxn = np.logical_not(Sx)
            dSdt[Sxn] = (UM.dot(F))[Sxn] + erlateral[Sxn]
            S[Sxn] += dSdt[Sxn] * dt

            # NB: in theory we should iterate this procedure until there are no
            # further cells with excess flow, but there is no guarantee that
            # the iteration process will converge.
        else:
            # No excess flow, so use the forward-Euler formula for all cells
            S += (dSdt * dt)

        # results from channel 
        Qout = F.copy()             
        Qout_channel_avg += Qout
        Qin_Channel_avg += Qin
        if res_flag==0:
            Favg += F
        # error = water_balance_channel_error(Qin[grdc_us_grids], 
		                                    # Qout[grdc_us_grids], 
											# Sin[grdc_us_grids], 
											# S[grdc_us_grids], 
											# dt, 
											# 0.5*(S[grdc_us_grids] + Sin[grdc_us_grids]))        
        # start of water management subroutines
        if res_flag==1: 
		    ### Reservoir Release and Storage Balance 
            Qin_resv  = F.copy()
            Rres   = F.copy()			
			# Reservoirs Release Calculation
			## Flood Control Reservoirs
            Rres[condF]  = flood_control_reservoir(cpa, ppose, Qin_resv, Sini_byr, 
																	mtifl, alpha, dt)
			## Irrigation Reservoirs
            Rres[condI]  = irrigation_reservoir(cpa, ppose, Qin_resv, Sini_byr, mtifl,
                                                  monthly_demand, mean_demand, alpha, dt)
			## Hydropower Reservoirs
            Rres[condH]  = hydropower_reservoir(cpa, ppose, Qin_resv, Sini_resv, Release_policy, maxTurbineFlow, dt)

            # Reservoir Water Balance         
            Qout_resv = Rres.copy()			
            Qout_resv[cond_all],  Sending[cond_all]  = reservoir_water_balance(Qin_resv[cond_all], 
			                                                                   Rres[cond_all], 
																			   Sini_resv[cond_all], 
																			   ppose[cond_all], 
																			   cpa[cond_all], 
																			   mtifl[cond_all], alpha, dt)   

            # error = water_balance_reservoir_error(Qin_resv[grdc_us_grids], 
			                                      # Qout_resv[grdc_us_grids], 
												  # Sini_resv[grdc_us_grids], 
												  # Sending[grdc_us_grids], 
												  # dt, 
												  # 0.5*(Sini_resv[grdc_us_grids]+Sending[grdc_us_grids]))
            # final step
            F = Qout_resv.copy()			
            Favg += F
            Qin_res_avg +=  Qin_resv.copy()
            Qout_res_avg += Qout_resv.copy()
            Sini_resv = Sending.copy()
        else:
            Favg += F

    # monthly stream flow	
    Favg /= nt
    # monthly channel inflow and out flow
    Qin_Channel_avg /= nt    
    Qout_channel_avg /= nt    
    # monthly reservoir inflow and release   			
    Qin_res_avg /= nt
    Qout_res_avg /= nt                               

    return S , Favg, F ,Qin_Channel_avg, Qout_channel_avg,  Qin_res_avg, Qout_res_avg, Sending
        


def downstream(coord, flowdir, settings):
    """Generate downstream cell ID matrix"""

    gridmap = np.zeros((settings.ngridrow, settings.ngridcol), dtype=int, order='F')
    # Insert grid cell ID to 2D grid index position
    gridmap[coord[:, 4].astype(int) - 1, coord[:, 3].astype(int) - 1] = coord[:, 0]

    gridlen = coord.shape[0]
    # ilat and ilon are the row and column numbers for each working cell in the full grid
    ilat = coord[:, 4].astype(int) - 1
    ilon = coord[:, 3].astype(int) - 1

    fdlat, fdlon = make_flowdirgrid(ilat, ilon, flowdir, gridlen)

    # Fix cells that are pointing off the edge of the full grid, if any.
    # Wrap the longitude
    bad = (fdlon < 0) | (fdlon > (settings.ngridcol - 1))
    fdlon[bad] = np.mod(fdlon[bad] + 1, settings.ngridcol)
    # Set bad latitudes to point at self, which will be detected as an outlet below.
    bad = (fdlat < 0) | (fdlat > (settings.ngridrow - 1))
    fdlat[bad] = ilat[bad]
    fdlon[bad] = ilon[bad]

    # Get index of the downstream cell.
    tmp = np.ravel_multi_index((fdlat, fdlon), (settings.ngridrow, settings.ngridcol), order='F')
    tmpGM = np.ravel(gridmap, order='F')
    dsid = tmpGM[tmp]

    # Mark cells that are outlets.  These are cells that point to a cell
    # outside the working set (i.e., an ocean cell) and cells that point at
    # themselves (i.e., has no flow direction).
    ocoutlet = (dsid == 0)
    selfoutlet = (dsid == coord[:, 0])
    dsid[ocoutlet | selfoutlet] = -1

    return dsid


def upstream(coord, downstream, settings):
    """Return a matrix of ngrid x 9 values.
    For each cell, the first 8 values are the cellIDs neighbor cells.
    The 9th is the number of neighbor cells that actually flow into the center cell.
    The neighbor cells are ordered so that the cells that flow into the center cell come first.
    Thus, if these are the columns in a row:

    id1 id2 id3 id4 id5 id6 id7 id8 N

    if N==3, then id1, id2, ad id3 flow into the center cell; the others don't.
    Many cells will not have a full complement of neighbors. These missing neighbors are given the ID 0 """

    gridmap = np.zeros((settings.ngridrow, settings.ngridcol), dtype=int, order='F')
    # Insert grid cell ID to 2D grid index position
    gridmap[coord[:, 4].astype(int) - 1, coord[:, 3].astype(int) - 1] = coord[:, 0]  # 1-67420

    glnrow = coord.shape[0]
    upcells = np.zeros((glnrow, 8), dtype=int)
    isupstream = np.zeros((glnrow, 8), dtype=bool)

    # Row and column offsets for the 8 possible neighbors.
    rowoff = [-1, -1, -1, 0, 0, 1, 1, 1]
    coloff = [-1, 0, 1, -1, 1, -1, 0, 1]

    for nbr in range(8):
        r = coord[:, 4].astype(int) - 1 + rowoff[nbr]
        c = coord[:, 3].astype(int) - 1 + coloff[nbr]
        goodnbr = (r >= 0) & (c >= 0) & (r <= settings.ngridrow - 1) & (c <= settings.ngridcol - 1)

        if goodnbr.any():
            tmp = np.ravel_multi_index(
                (r[goodnbr], c[goodnbr]), (settings.ngridrow, settings.ngridcol), order='F')
            tmpGM = np.ravel(gridmap, order='F')
            upcells[goodnbr, nbr] = tmpGM[tmp]

        # Some cells have a zero in the grid map, indicating they are not being
        # tracked (they are ocean cells or some such.  Reset the mask to
        # reflect only the cells that are 'real' neighbors.
        goodnbr = np.logical_not(upcells[:, nbr] == 0)

        # Determine which cells flow into the center cell
        goodCoord = coord[goodnbr, 0].astype(int)
        goodDownstream = downstream[upcells[goodnbr, nbr] - 1]
        isupstream[goodnbr, nbr] = np.equal(goodCoord, goodDownstream)

    # Sort the neighbor cells so that the upstream ones come first.
    try:
        permvec = np.argsort(-isupstream)  # Sort so that True values are first
    except TypeError:
        # for newer versions of NumPy
        permvec = np.argsort(~isupstream)

    isupstream.sort()
    isupstream = isupstream[:, ::-1]

    ndgrid, _ = np.mgrid[0:glnrow, 0:8]  # Get necessary row adder
    permvec = permvec * glnrow + ndgrid

    tmpU = upcells.flatten('F')
    tmpP = permvec.flatten('F')
    tmpFinal = tmpU[tmpP]
    tmpFinal = tmpFinal.reshape((glnrow, 8), order='F')

    # Count the number of upstream cells.
    cellCount = np.zeros((glnrow, 1), dtype=int)
    cellCount[:, 0] = np.sum(isupstream, axis=1)
    upcells = np.concatenate((tmpFinal, cellCount), axis=1)

    return upcells

def upstream_genmatrix(upid):
    """Generate a sparse matrix representation of the upstream cells for each cell.
    The RHS of the ODE for channel storage S can be writen as
    dS/dt = UP * F + erlateral - S / T
    Since the instantaneous channel flow, F = S / T, this is the same as:
    dS/dt = [UP - I] S / T + erlateral
    This function returns UM = UP - I
    The second argument is the Jacobian matrix, J."""

    N = upid.shape[0]

    # Preallocate the sparse matrix.
    # Since we know that each cell flows into at most one other cell (some don't flow into any),
    # we can be sure we will need at most N nonzero slots.
    ivals = np.zeros((N,), dtype=int)
    jvals = np.zeros((N,), dtype=int)
    lb = 0  # Lower bound: the first index for each group of entries

    for i in range(N):
        numUp = upid[i, 8]  # Number of upstream cells for the current cell
        if numUp > 0:  # Skip if no upstream cells
            ub = lb + numUp
            jvals[lb:ub] = upid[i, 0:numUp]
            ivals[lb:ub] = i + 1
            lb = ub

    data = np.ones_like(ivals[0:ub])
    row = ivals[0:ub] - 1
    col = jvals[0:ub] - 1
    
    UP = sparse.coo_matrix((data, (row, col)), shape=(N, N)) 
    UM = sparse.coo_matrix((data, (row, col)), shape=(N, N)) - sparse.eye(N, dtype=int)

    return UM, UP



def make_flowdirgrid(ilat, ilon, flowdir, gridlen):
    # These are the bitwise and values of all the flow codes that lead in each
    # of the four directions.  'Up' and 'down' refer to directions in our grid
    # (i.e., they add to lat for 'up' and subtract for 'down')
    rt = 1 + 2 + 2 ** 7
    lt = 2 ** 3 + 2 ** 4 + 2 ** 5
    up = 2 ** 5 + 2 ** 6 + 2 ** 7
    dn = 2 + 2 ** 2 + 2 ** 3

    # Calculate the offset
    flwdr = np.copy(flowdir)
    flwdr[flowdir == -9999.] = 0
    flwdr = flwdr.astype(int)

    fdlat = np.zeros((gridlen,), dtype=int)
    fdlon = np.zeros((gridlen,), dtype=int)
    fdlat[(dn & flwdr) != 0] = -1
    fdlat[(up & flwdr) != 0] = 1
    fdlon[(rt & flwdr) != 0] = 1
    fdlon[(lt & flwdr) != 0] = -1

    # Apply the offset to latitude and longitude
    fdlat = fdlat + ilat
    fdlon = fdlon + ilon

    return fdlat, fdlon


######################################################################
#                       NEW SUBROUTINES                              #
######################################################################
# reservoir water balance error
def water_balance_reservoir_error(Qin, Qout, Sin, Sout, dt, Snorm):
    """Computes reservoir water balance error, acceptable erro is <=1e-6"""    
    DSdt_Q = (Qin - Qout)*dt
    DSdt_S = (Sout - Sin)
    DS_diff = np.abs(DSdt_Q - DSdt_S)
    Res_Wbalance_relative_error = np.zeros_like(DS_diff)
    Sxs = (Snorm < 1)   
    if Sxs.any():       
        Res_Wbalance_relative_error[Sxs] = DS_diff[Sxs]
       
    Szs = np.logical_not(Sxs)
    Res_Wbalance_relative_error[Szs] = np.divide(DS_diff[Szs],  Snorm[Szs])
    
    if np.max(Res_Wbalance_relative_error[Szs]) > 1e-6:
        print('Error: Reservoir water balance violated') 
        
        import sys
        sys.exit()      
       
    return Res_Wbalance_relative_error      

     
# channel water balance error
def water_balance_channel_error(Qin, Qout, Sin, Sout, dt):
    """Computes channel water balance error, acceptable erro is <=1e-6"""      
    Snorm = 0.5*(Sout + Sin)
    DSdt_Q = (Qin - Qout)*dt
    DSdt_S = (Sout - Sin)
    DS_diff = np.abs(DSdt_Q - DSdt_S)
    
    Wbalance_relative_error = np.divide(DS_diff,  Snorm)    
    Szs = ~(Snorm < 1)    
    if np.max(Wbalance_relative_error[Szs]) > 1e-6:
        print('Error: channel water balance violated') 

        import sys
        sys.exit()     
    return Wbalance_relative_error      


     
# channel water balance error
def channel_water_balance_error(Qin, Qout, Sin, Sout, dt):
    """Computes channel water balance error, acceptable erro is <=1e-6"""  
    Snorm = 0.5*(Sout + Sin)    
    DSdt_Q = (Qin - Qout)*dt # change in storage from flow
    DSdt_S = (Sout - Sin)    # change in storage from storage
    DS_diff = np.abs(DSdt_Q - DSdt_S) #difference between the two

    #Ch_Wbalance_relative_error = np.zeros_like(DS_diff)  
    Ch_Wbalance_relative_error = np.divide(DS_diff,  Snorm)     
    # if the DS_diff < 1e-1 in m3, it can simply be ignored, else 
    #Sxs = ~(DS_diff < 1e-1)   

    #Ch_Wbalance_relative_error[~Sxs] = DS_diff[~Sxs]/dt
    if np.max(Ch_Wbalance_relative_error) > 1e-6:
        print(np.max(Ch_Wbalance_relative_error))  
        TXT1 = Snorm
        TXT2 = Qin  
        TXT3 = Qout         
        TXT4 = Sin
        TXT5 = Sout              
        xx = np.where(Ch_Wbalance_relative_error==np.max(Ch_Wbalance_relative_error))[0]
        print(TXT1[xx])      
        print(TXT2[xx])  
        print(TXT3[xx])       
        print(TXT4[xx])  
        print(TXT5[xx])                     
        print('Error: channel water balance violated') 

        import sys
        sys.exit()     

    return np.max(Ch_Wbalance_relative_error)    
######################################################################
# WATER MANAGEMENT 
# Irrigation Release
def irrigation_reservoir(cpa, ppose, qin, Sini, mtifl,
                         wdirr, irrmean, alpha, dt):
    """Computes release from irrigation reservoirs"""  
    #irrigation Reservoirs
    condI = np.where((cpa != 0) & (ppose ==2))[0] # irrigation reservoir cells

    # initialization
    Nx = len(condI)
    Rprovisional = np.zeros([Nx,]) #Provisional Release
    Rirrg_final = np.zeros([Nx,])       #Final Release

    # water management
    monthly_demand = wdirr[condI] #downstream  demand: m^3/s
    mean_demand  = irrmean[condI] #downstream mean demand:  m^3/s
    mtifl_irr = mtifl[condI]      #mean flow:  m^3/s
    cpa_irr = cpa[condI]          #capacity: 1e6 m^3
    qin_irr = qin[condI]
    Sbegnining_ofyear = Sini[condI]

    #****** Provisional Release *******
    # mean demand & annual mean inflow
    m = mean_demand - (0.5 * mtifl_irr)  
    cond1 = np.where(m >= 0 )[0] # m >=0 ==> dmean > = 0.5*annual mean inflow
    cond2 = np.where(m < 0)[0]   # m < 0 ==> dmean <  0.5*annual mean inflow    

    # Provisional Release 
    demand_ratio = np.divide(monthly_demand[cond1] , mean_demand[cond1])
    Rprovisional[cond1] = np.multiply(0.5*mtifl_irr[cond1],  (1 + demand_ratio)) # Irrigation dmean >= 0.5*imean        
    Rprovisional[cond2] = mtifl_irr[cond2] + monthly_demand[cond2] - mean_demand[cond2]  # Irrigation dmean < 0.5*imean   

    #******  Final Release ****** 
    # capacity & annual total infow
    c = np.divide(cpa_irr, ( mtifl_irr * 365 * 24 * 3600))
    cond3 = np.where(c >= 0.5)[0] #c = capacity/imean >= 0.5
    cond4 = np.where(c < 0.5)[0]  #c = capacity/imean < 0.5 

    # c = capacity/imean >= 0.5
    Krls = np.divide(Sbegnining_ofyear,(alpha * cpa_irr)) 
    Rirrg_final[cond3] = np.multiply(Krls[cond3], mtifl_irr[cond3])
    # c = capacity/imean < 0.5  
    temp1 = (c[cond4]/0.5)**2
    temp2 = np.multiply(temp1,Krls[cond4])
    temp3 = np.multiply(temp2,Rprovisional[cond4])      
    temp4 = np.multiply((1 - temp1), mtifl_irr[cond4])
    Rirrg_final[cond4] = temp3 + temp4

    return Rirrg_final 

# Flood Control Release
def flood_control_reservoir(cpa, ppose, qin, Sini, mtifl, alpha, dt):
    """Computes release from flood control reservoirs"""  
    #irrigation Reservoirs
    condF = np.where((cpa != 0) & (ppose ==3))[0] # flood reservoir cells

    # initialization
    Nx = len(condF)
    Rprovisional = np.zeros([Nx,]) #Provisional Release
    Rflood_final = np.zeros([Nx,])       #Final Release

    # water management
    mtifl_flood = mtifl[condF]      #mean flow:  m^3/s
    cpa_flood = cpa[condF]          #capacity:   m^3
    qin_flood = qin[condF]          #mean flow:  m^3/s
    Sbegnining_ofyear = Sini[condF] #capacity:   m^3

    # Provisional Release 
    Rprovisional = mtifl_flood.copy()

    # Final Release 
    # capacity & annual total infow
    c = np.divide(cpa_flood, ( mtifl_flood * 365 * 24 * 3600))
    cond1 = np.where(c >= 0.5)[0] #c = capacity/imean >= 0.5
    cond2 = np.where(c < 0.5)[0]  #c = capacity/imean < 0.5 

    # c = capacity/imean >= 0.5
    Krls = np.divide(Sbegnining_ofyear,(alpha * cpa_flood)) 
    Rflood_final[cond1] = np.multiply(Krls[cond1], mtifl_flood[cond1])
    # c = capacity/imean < 0.5  
    temp1 = (c[cond2]/0.5)**2
    temp2 = np.multiply(temp1,Krls[cond2])
    temp3 = np.multiply(temp2,Rprovisional[cond2])      
    temp4 = np.multiply((1 - temp1), mtifl_flood[cond2])
    Rflood_final[cond2] = temp3 + temp4


    return Rflood_final  
 		

# Hydropower Release    
def hydropower_reservoir(cpa, ppose, qin,  Sin_current, Rpolicy, maxTurbineFlow, dt):
    """Computes release from hydropower reservoirs"""      
    # setups
    r_disc = 10 # flow discretization levels
    s_disc = 1000 # storage discretization levels
    
    # data 
    condH = np.where((cpa != 0) & (ppose ==1))[0] # HP reservoir cells
    Nx = len(condH)
	
    qinflow = qin[condH] #inflow to reservoir
    cpaH = cpa[condH] # reservoir capacity M3
    Scurrent = Sin_current[condH] # current reservoir storage
    maxTFlow = maxTurbineFlow[condH] # maximum turbine flow

    ## simulate release
    # initial storage
    Sinix = np.reshape(np.tile(Scurrent,1001), [1001,len(cpaH)])  
    # maximum turbineflow discretization
    r_disc_x = np.linspace(0, maxTFlow, r_disc+1)
    # storage discretization
    s_states = np.linspace(0, cpaH, s_disc+1)    
    # find discretized and current storage absolute difference
    s_difference = np.abs(s_states - Sinix)
    # find where difference is minimum
    Sstate = np.where(s_difference== np.min(s_difference, axis=0))[0]
    # obtain release policy from lookup table for Sstate
    RpolicyCh = np.array([Rpolicy[Sstate[ii],ii] for ii in range(Nx)]).astype(np.int64)
    # provisional release
    Rprov = np.array([r_disc_x[RpolicyCh[ii],ii] for ii in range(Nx)]) 
    # readjusted release
    ReleaseHp = np.array([min(Rprov[ii], (Scurrent[ii]/dt) + qinflow[ii]) for ii in range(Nx)])

    return ReleaseHp


# Hydropower Optimal Policy    	
def optimal_release_policy(res_data, inflow, res):
    """
    Hydropower reservoirs release policy optimization function
    (BACKWARDS RECURSIVE PROCEDURE)
    """
    
    secs_in_month = 2629800  # number of seconds in an average month
    cumecs_to_Mm3permonth = 2.6298  # m3/s to Mm3/month
    sww = 9810  # specific weight of water (N/m^3)
    hours_in_year = 8766  # number of hours in a year
    mwh_to_exajoule = 3.6 * (10 ** -9)  # megawatts to exajoules
    mths = 12  # months in year
    
    ########################
    capp = res_data["CAP"][res]*1e-6 #MCM
    cap_live = res_data["CAP"][res]*1e-6 #MCM  
    installed_cap = res_data["ECAP"][res]
    q_max = res_data["FLOW_M3S"][res] * cumecs_to_Mm3permonth
    efficiency = 0.9
    surface_area = res_data["AREA"][res]
    max_depth = res_data["DAM_HGT"][res]
    head = max_depth
    if np.isnan(head) == True:
        head = installed_cap / (efficiency * sww * (q_max / secs_in_month))
        max_depth = installed_cap / (efficiency * sww * (q_max / secs_in_month))
    if np.isnan(q_max)==True:
        qmax = (installed_cap / (efficiency * sww * head)) * secs_in_month
        
    #########
    r_disc = 10
    s_disc = 1000
    s_states = np.linspace(0, capp, s_disc+1)  # storage discretized to 1000 segments for stoch. dyn. prog.
    r_disc_x = np.linspace(0, q_max, r_disc+1)
    m = mths
    q_Mm3 = inflow[res,:]*cumecs_to_Mm3permonth
    inflow = pd.DataFrame(q_Mm3)
    
    from datetime import date
    start_date = date(1971, 1, 1)  # Get start date for simulation "M/YYYY"
    inflow_ts = inflow.set_index(pd.period_range(start_date, periods = len(inflow), freq = "M"))
    q_disc = np.array((0, 0.2375, 0.4750, 0.7125, 0.95, 1))
    q_probs = np.diff(q_disc)  # probabilities for each q class
    q_class_med = inflow_ts.groupby(inflow_ts.index.month).quantile(q_disc[1:6] - (q_probs / 2))
    # set up empty arrays to be populated
    shell_array = np.zeros(shape=(len(q_probs), len(s_states), len(r_disc_x)))
    rev_to_go = np.zeros(len(s_states))
    #r_policy = np.zeros([len(s_states), m])
    #Bellman = np.zeros([len(s_states), m])
    r_policy_test = np.zeros([len(s_states), m])
    # work backwards through months of year (12 -> 1) and repeat till policy converges  
    while True:
        r_policy = np.zeros([len(s_states), m])
        for t in range(m, 0, -1):
            r_cstr = shell_array + np.array(q_class_med.loc[t, slice(None)][0])[:, np.newaxis][:, np.newaxis] + \
                     shell_array + s_states[:, np.newaxis]  # constrained releases
            r_star = shell_array + r_disc_x   # desired releases
            s_nxt_stage = r_cstr - r_star
            s_nxt_stage[s_nxt_stage < 0] = 0
            s_nxt_stage[s_nxt_stage > capp] = capp
            y, yconst, cap_live = GetLevel(max_depth, head, surface_area, capp, cap_live, (s_nxt_stage + s_states[:, np.newaxis])*1e6 / 2)
            h_arr = y + yconst
            #get head for all storage states for revenue calculation
            rev_arr = np.multiply(h_arr, r_star)  # revenue taken as head * release
            implied_s_state = np.around(1 + (s_nxt_stage / capp) * (len(s_states) - 1)).astype(int)
            #implied storage is the storage implied by each release decision and inflow combination
            rev_to_go_arr = rev_to_go[implied_s_state - 1]
            max_rev_arr = rev_arr + rev_to_go_arr
            max_rev_arr_weighted = max_rev_arr * np.array(q_probs)[:, np.newaxis][:, np.newaxis]
            max_rev_arr_weighted[r_star > r_cstr] = float("-inf")  # negative rev to reject non-feasible release
            max_rev_expected = max_rev_arr_weighted.sum(axis=0)
            rev_to_go = max_rev_expected.max(1)
            r_policy[:, t - 1] = np.argmax(max_rev_expected, 1)
        pol_test = float(sum(sum(r_policy == r_policy_test))) / (m * len(s_states))
        r_policy_test = r_policy  # re-assign policy test for next loop test
        if pol_test >= 0.99:
            break
    return r_policy	

         
def GetLevel(max_depth, head, surface_area, capac, cap_live, V):  
    """Computes storage level for hydropower reservoirs"""     

    if np.isnan(max_depth) == True:
        c = (np.sqrt(2) / 3 )* ((surface_area * 1e6) ** (3/2)) / (capac * 1e6)
        y = (6 * V / (c**2)) ** (1 / 3)
        yconst = head - y
        if (yconst < 0):
            cap_live = np.nanmin([cap_live, capac - (((-yconst) ** 3) * (c ** 2 / 6 / 1e6))])
    else:
        c = 2 * capac / (max_depth * surface_area)
        y = max_depth * (V / (capac* 1e6))**(c / 2)
        yconst = head - max_depth      
        if (yconst < 0):
            cap_live = np.nanmin([cap_live, capac- ((-yconst /max_depth) ** (2 /c) * capac)])

    return y, yconst, cap_live
    
    

# Reservoir water balance
def reservoir_water_balance(Qin, Qout, Sin,  ppose, cpa, mtifl, alpha, dt):
    """Re-adjusts release for environmental flow (if necessary) and 
          Computes the storage level after release for all types of reservoirs"""     

    resrvoirs_indx = np.where(ppose>0)[0]
	# inputs
    Qin_resd = Qin[resrvoirs_indx]
    Qout_resd= Qout[resrvoirs_indx]
    Sin_resd = Sin[resrvoirs_indx]
    cpa_resd = cpa[resrvoirs_indx]
    mtifl_res= mtifl[resrvoirs_indx]

    # final storage and release initialization	
    Nx = len(resrvoirs_indx)
    Rfinal = Qout_resd.copy()    # final release
    Sfinal = np.zeros([Nx,])    # final storage
    diff_rt= np.zeros([Nx,])   # change in storage

    # environmental flow   
    diff_rt = Qout_resd - (mtifl_res*0.1)
    indx_rt = np.where(diff_rt < 0)[0]
    Rfinal[indx_rt] = 0.1*mtifl_res[indx_rt]

    # storage  
    dsdt_resv = (Qin_resd - Rfinal)*dt
    Stemp = Sin_resd + dsdt_resv

    # condition a : storage > capacity
    Sa = (Stemp > (alpha*cpa_resd))
    Sfinal[Sa] = alpha*cpa_resd[Sa]
    Rspill     = (Stemp[Sa] - Sfinal[Sa])/dt
    Rfinal[Sa] = Qout_resd[Sa] + Rspill

    # condition b : storage <= 0
    Sb = (Stemp <= 0)
    Sfinal[Sb] = 0
    Rfinal[Sb] = (Sin_resd[Sb]/dt) + Qin_resd[Sb]

    # condition c : 25% capacity < S < capacity
    Sc = ((Stemp > 0) & (Stemp <= alpha*cpa_resd))
    Sfinal[Sc] = Stemp[Sc]
    Rfinal[Sc] = Rfinal[Sc] 

    return Rfinal,  Sfinal 

def grdc_stations_upstreamgrids(upid, gridID):
    operate = [gridID]
    results = [gridID]
    while not (len(operate)==0) :
        grd_us = operate[0]
        immediate_us = upid[grd_us,0:upid[grd_us,8]]
        for ii in range(len(immediate_us)):
            results.append(immediate_us[ii]-1)
            operate.append(immediate_us[ii]-1)
        operate.remove(operate[0])
    
    contributing_grids = results

    return contributing_grids       