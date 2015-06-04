import numpy as np
import ephem
from lsst.sims.maf.utils.telescopeInfo import TelescopeInfo
from lsst.sims.utils import haversine, altAzToRaDec
import warnings
from lsst.sims.skybrightness.utils import wrapRA,  mjd2djd, raDecToAltAz
from .interpComponents import ScatteredStar,Airglow,LowerAtm,UpperAtm,MergedSpec,TwilightInterp,MoonInterp,ZodiacalInterp
from lsst.sims.photUtils import Sed

def justReturn(input):
    """
    really, just return the input
    """
    return input


class SkyModel(object):

    def __init__(self, observatory='LSST',
                 twilight=True, zodiacal=True,  moon=True,
                 lowerAtm=False, upperAtm=False, airglow=False, scatteredStar=False,
                 mergedSpec=True, verbose=True):
        """By default, assume this is for LSST site, otherwise expect an observatory object
        with attributes lat,lon.elev"""

        self.moon=moon
        self.lowerAtm = lowerAtm
        self.twilight = twilight
        self.zodiacal = zodiacal
        self.upperAtm = upperAtm
        self.airglow = airglow
        self.scatteredStar = scatteredStar
        self.mergedSpec = mergedSpec
        self.verbose = verbose

        self.components = {'moon':self.moon, 'lowerAtm':self.lowerAtm, 'twilight':self.twilight,
                           'upperAtm':self.upperAtm, 'airglow':self.airglow,'zodiacal':self.zodiacal,
                           'scatteredStar':self.scatteredStar, 'mergedSpec':self.mergedSpec}

        # Check that the merged component isn't being run with other components
        mergedComps = [self.lowerAtm, self.upperAtm, self.airglow, self.scatteredStar]
        for comp in mergedComps:
            if comp & self.mergedSpec:
                warnings.warn("Adding component multiple times to the final output spectra.")



        interpolators = {'scatteredStar':ScatteredStar, 'airglow':Airglow, 'lowerAtm':LowerAtm,
                         'upperAtm':UpperAtm, 'mergedSpec':MergedSpec, 'moon':MoonInterp,
                         'zodiacal':ZodiacalInterp, 'twilight':TwilightInterp}

        # Load up the interpolation objects for each component
        self.interpObjs = {}
        for key in self.components:
            if self.components[key]:
                self.interpObjs[key] = interpolators[key]()


        # Set up a pyephem observatory object
        if observatory == 'LSST':
            self.telescope = TelescopeInfo(observatory)
            self.Observatory = ephem.Observer()
            self.Observatory.lat = self.telescope.lat
            self.Observatory.lon = self.telescope.lon
            self.Observatory.elevation = self.telescope.elev
        else:
            self.Observatory = observatory


    def setRaDecMjd(self, ra,dec,mjd, degrees=False, azAlt=False):
        """
        Set the sky parameters by computing the sky conditions on a given MJD and sky location.

        Ra and Dec in raidans or degrees.
        input ra, dec or az,alt w/ altAz=True
        """
        # Wrap in array just in case single points were passed
        if not type(ra).__module__ == np.__name__ :
            if np.size(ra) == 1:
                self.ra = np.array([ra])
                self.dec = np.array([dec])
            else:
                self.ra = np.array(ra)
                self.dec = np.array(dec)
        if degrees:
            self.ra = np.radians(ra)
            self.dec = np.radians(dec)
        else:
            self.ra = ra
            self.dec = dec
        self.mjd = mjd
        if azAlt:
            self.azs = ra.copy()
            self.alts = dec.copy()
            # I think there's a bug in this!!!
            warnings.warn('I think altAzToRaDec is broken!')
            self.ra,self.dec = altAzToRaDec(self.alts,self.azs, self.Observatory.lon,
                                            self.Observatory.lat, self.mjd)
        else:
            # calc airmass for each point
            # XXX-check this damn thing!
            self.alts,self.azs = raDecToAltAz(self.ra, self.dec, self.Observatory.lon,
                                                   self.Observatory.lat, self.mjd)

        self.npts = self.ra.size

        names = ['airmass', 'nightTimes', 'alt', 'az', 'azRelMoon', 'moonSunSep', 'moonAltitude',
                 'altEclip', 'azEclipRelSun', 'sunAlt', 'azRelSun']
        types = [float]*len(names)
        self.points = np.zeros(self.npts, zip(names,types))

        # Now to set the relevant parameters

        # Switch to Dublin Julian Date for pyephem
        self.Observatory.date = mjd2djd(self.mjd)

        sun = ephem.Sun()
        sun.compute(self.Observatory)
        self.sunAlt = sun.alt
        self.sunAz = sun.az

        # Compute airmass the same way as ESO model
        self.airmass = 1./np.cos(np.pi/2.-self.alts)

        self.points['airmass'] = self.airmass
        self.points['nightTimes'] = 2
        self.points['alt'] = self.alts
        self.points['az'] = self.azs

        if self.twilight:
            self.points['sunAlt'] = self.sunAlt
            self.points['azRelSun'] = wrapRA(self.azs - self.sunAz)

        if self.moon:
            moon = ephem.Moon()
            moon.compute(self.Observatory)
            self.moonPhase = moon.phase
            self.moonAlt = moon.alt
            self.moonAz = moon.az
            # Calc azimuth relative to moon
            self.azRelMoon = wrapRA(self.azs - self.moonAz)
            over = np.where(self.azRelMoon > np.pi)
            self.azRelMoon[over] = 2.*np.pi - self.azRelMoon[over]
            self.points['moonAltitude'] += np.degrees(self.moonAlt)
            self.points['azRelMoon'] += self.azRelMoon
            self.points['moonSunSep'] += self.moonPhase/100.*180.


        if self.zodiacal:
            self.eclipLon = np.zeros(self.npts)
            self.eclipLat = np.zeros(self.npts)

            for i,temp in enumerate(self.ra):
                eclip = ephem.Ecliptic(ephem.Equatorial(self.ra[i],self.dec[i], epoch='2000'))
                self.eclipLon[i] += eclip.lon
                self.eclipLat[i] += eclip.lat
            # Subtract off the sun ecliptic longitude
            sunEclip = ephem.Ecliptic(sun)
            self.sunEclipLon = sunEclip.lon
            self.points['altEclip'] += self.eclipLat
            self.points['azEclipRelSun'] += wrapRA(self.eclipLon - self.sunEclipLon)

    def setParams(self, airmass=1.,azs=90., alts=None, moonPhase=31.67, moonAlt=45.,
                  moonAz=0., sunAlt=-12., sunEclipLon=0.,
                  eclipLon=135., eclipLat=90., degrees=True):
        """
        Set paramters manually. Note, you can put in unphysical combinations of paramters if you want
        to (e.g., put a full moon at zenith at sunset).
        if the alts kwarg is set it will override the airmass kwarg.
        MoonPhase is percent of moon illuminated (0-100)
        """

        # Convert all values to radians for internal use.
        if degrees:
            convertFunc = np.radians
        else:
            convertFunc = justReturn

        self.sunAlt = convertFunc(sunAlt)
        self.moonPhase = moonPhase
        self.moonAlt = convertFunc(moonAlt)
        moonAz = convertFunc(moonAz)
        self.eclipLon = convertFunc(eclipLon)
        self.eclipLat = convertFunc(eclipLat)
        self.sunEclipLon = convertFunc(sunEclipLon)
        azs = convertFunc(azs)
        if alts is not None:
            self.airmass = 1./np.cos(np.pi/2.-convertFunc(alts))
            alts = convertFunc(alts)
        else:
            self.airmass = airmass
            alts = np.pi/2.-np.arccos(1./airmass)
        self.moonTargSep = haversine(azs, alts, moonAz, self.moonAlt)
        self.npts = np.size(airmass)

    def computeSpec(self, npix = 17001):
        # set up array to hold the resulting spectra for each ra,dec point.
        self.spec = np.zeros((self.npts, npix), dtype=float)

        # Rebuild the components dict so things can be turned on/off
        self.components = {'moon':self.moon, 'lowerAtm':self.lowerAtm, 'twilight':self.twilight,
                           'upperAtm':self.upperAtm, 'airglow':self.airglow,'zodiacal':self.zodiacal,
                           'scatteredStar':self.scatteredStar, 'mergedSpec':self.mergedSpec}

        for key in self.components:
            if self.components[key]:
                result = self.interpObjs[key](self.points)
                self.spec += result['spec']
                if not hasattr(self,'wave'):
                    self.wave = result['wave']
                else:
                    if not np.array_equal(result['wave'], self.wave):
                        warnings.warn('Wavelength arrays of components do not match.')


    def computeMags(self, throughput):
        """After the spectra have been computed, optionally convert to mags"""
        self.mags = np.zeros(self.npts, dtype=float)-666
        tempSed = Sed()
        for i, ra in enumerate(self.ra):
            if np.max(self.spec[i,:]) > 0:
                tempSed.setSED(self.wave, flambda=self.spec[i,:])
                # Need to try/except because the spectra might be zero in the filter
                try:
                    self.mags[i] = tempSed.calcMag(throughput)
                except:
                    pass
        return self.mags