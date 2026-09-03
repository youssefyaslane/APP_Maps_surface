--
-- PostgreSQL database dump
--

\restrict qKnC3VMdSZalPzsz3n3mGwHQFgHkSMTIhfN5ZCFiYCywWyJAE6Bt3dVqC4W2wDz

-- Dumped from database version 16.14
-- Dumped by pg_dump version 16.14

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: companies; Type: TABLE; Schema: public; Owner: maps
--

CREATE TABLE public.companies (
    id integer NOT NULL,
    name text NOT NULL,
    category text,
    address text,
    city text,
    phone text,
    email text,
    website text,
    rating double precision,
    lon double precision NOT NULL,
    lat double precision NOT NULL,
    place_id text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    roof_area_m2 double precision,
    roof_source text,
    solar_panels integer,
    solar_kwc double precision,
    solar_computed_at timestamp with time zone
);


ALTER TABLE public.companies OWNER TO maps;

--
-- Name: companies_id_seq; Type: SEQUENCE; Schema: public; Owner: maps
--

CREATE SEQUENCE public.companies_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.companies_id_seq OWNER TO maps;

--
-- Name: companies_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: maps
--

ALTER SEQUENCE public.companies_id_seq OWNED BY public.companies.id;


--
-- Name: ia_segments; Type: TABLE; Schema: public; Owner: maps
--

CREATE TABLE public.ia_segments (
    id integer NOT NULL,
    polygon jsonb NOT NULL,
    area_m2 double precision NOT NULL,
    centroid_lon double precision NOT NULL,
    centroid_lat double precision NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    source text DEFAULT 'ia-segmentation'::text NOT NULL
);


ALTER TABLE public.ia_segments OWNER TO maps;

--
-- Name: ia_segments_id_seq; Type: SEQUENCE; Schema: public; Owner: maps
--

CREATE SEQUENCE public.ia_segments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.ia_segments_id_seq OWNER TO maps;

--
-- Name: ia_segments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: maps
--

ALTER SEQUENCE public.ia_segments_id_seq OWNED BY public.ia_segments.id;


--
-- Name: ms_buildings; Type: TABLE; Schema: public; Owner: maps
--

CREATE TABLE public.ms_buildings (
    id integer NOT NULL,
    polygon jsonb NOT NULL,
    area_m2 double precision NOT NULL,
    centroid_lon double precision NOT NULL,
    centroid_lat double precision NOT NULL
);


ALTER TABLE public.ms_buildings OWNER TO maps;

--
-- Name: ms_buildings_id_seq; Type: SEQUENCE; Schema: public; Owner: maps
--

CREATE SEQUENCE public.ms_buildings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.ms_buildings_id_seq OWNER TO maps;

--
-- Name: ms_buildings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: maps
--

ALTER SEQUENCE public.ms_buildings_id_seq OWNED BY public.ms_buildings.id;


--
-- Name: companies id; Type: DEFAULT; Schema: public; Owner: maps
--

ALTER TABLE ONLY public.companies ALTER COLUMN id SET DEFAULT nextval('public.companies_id_seq'::regclass);


--
-- Name: ia_segments id; Type: DEFAULT; Schema: public; Owner: maps
--

ALTER TABLE ONLY public.ia_segments ALTER COLUMN id SET DEFAULT nextval('public.ia_segments_id_seq'::regclass);


--
-- Name: ms_buildings id; Type: DEFAULT; Schema: public; Owner: maps
--

ALTER TABLE ONLY public.ms_buildings ALTER COLUMN id SET DEFAULT nextval('public.ms_buildings_id_seq'::regclass);


--
-- Name: companies companies_pkey; Type: CONSTRAINT; Schema: public; Owner: maps
--

ALTER TABLE ONLY public.companies
    ADD CONSTRAINT companies_pkey PRIMARY KEY (id);


--
-- Name: companies companies_place_id_key; Type: CONSTRAINT; Schema: public; Owner: maps
--

ALTER TABLE ONLY public.companies
    ADD CONSTRAINT companies_place_id_key UNIQUE (place_id);


--
-- Name: ia_segments ia_segments_pkey; Type: CONSTRAINT; Schema: public; Owner: maps
--

ALTER TABLE ONLY public.ia_segments
    ADD CONSTRAINT ia_segments_pkey PRIMARY KEY (id);


--
-- Name: ms_buildings ms_buildings_pkey; Type: CONSTRAINT; Schema: public; Owner: maps
--

ALTER TABLE ONLY public.ms_buildings
    ADD CONSTRAINT ms_buildings_pkey PRIMARY KEY (id);


--
-- Name: idx_companies_coords; Type: INDEX; Schema: public; Owner: maps
--

CREATE INDEX idx_companies_coords ON public.companies USING btree (lat, lon);


--
-- Name: idx_companies_solar_kwc; Type: INDEX; Schema: public; Owner: maps
--

CREATE INDEX idx_companies_solar_kwc ON public.companies USING btree (solar_kwc DESC NULLS LAST);


--
-- Name: idx_ia_segments_centroid; Type: INDEX; Schema: public; Owner: maps
--

CREATE INDEX idx_ia_segments_centroid ON public.ia_segments USING btree (centroid_lat, centroid_lon);


--
-- Name: idx_ms_buildings_centroid; Type: INDEX; Schema: public; Owner: maps
--

CREATE INDEX idx_ms_buildings_centroid ON public.ms_buildings USING btree (centroid_lat, centroid_lon);


--
-- PostgreSQL database dump complete
--

\unrestrict qKnC3VMdSZalPzsz3n3mGwHQFgHkSMTIhfN5ZCFiYCywWyJAE6Bt3dVqC4W2wDz

